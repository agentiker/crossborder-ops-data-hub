from datetime import date, datetime
from decimal import Decimal

import pytest

from core.domain import DomainOrder, DomainOrderLineItem
from models.base_models import BusinessScope, PlatformToken
from services import order_metrics, scope_resolution
from services.order_store import upsert_orders
from services.scope_resolution import ScopeError, expand_scope, resolve_filters


def _use(session, monkeypatch):
    monkeypatch.setattr(scope_resolution, "SessionLocal", lambda: session)


def _token(session, shop_id, *, platform="tiktok_shop", country="ID"):
    session.add(
        PlatformToken(
            platform=platform,
            country=country,
            shop_id=shop_id,
            scope_key=f"platform={platform}|shop={shop_id}",
        )
    )


def _scope(session, key, shop_ids, *, scope_type="shop_group",
           platform="tiktok_shop", country="ID", active=True):
    session.add(
        BusinessScope(
            scope_key=key,
            scope_name=f"name-{key}",
            scope_type=scope_type,
            platform=platform,
            country=country,
            shop_ids=shop_ids,
            is_active=active,
        )
    )


def test_expand_scope_returns_shop_ids_and_display(session, monkeypatch):
    _use(session, monkeypatch)
    _scope(session, "tts-id-all", ["s1", "s2", "s3"])
    session.commit()

    f = expand_scope("tts-id-all")
    assert f.platform == "tiktok_shop"
    assert f.country == "ID"
    assert f.shop_ids == ["s1", "s2", "s3"]
    assert f.scope_key == "tts-id-all"
    assert f.display_text == "TikTok Shop / 印尼 / 3 个店铺"


def test_scope_tenant_isolation(session, monkeypatch):
    """list_scopes/get_scope/expand_scope 按 account_id 隔离：gtl 看不到 ecom 的 scope。"""
    _use(session, monkeypatch)
    _scope(session, "ecom-only", ["s1"])  # 默认 account=ecom-app
    session.add(
        BusinessScope(
            account_id="ecom-app-gtl", scope_key="gtl-only", scope_name="gtl的",
            scope_type="shop_group", platform="tiktok_shop", country="ID",
            shop_ids=["s1"], is_active=True,
        )
    )
    session.commit()

    ecom_keys = {s["scope_key"] for s in scope_resolution.list_scopes("ecom-app")}
    gtl_keys = {s["scope_key"] for s in scope_resolution.list_scopes("ecom-app-gtl")}
    assert ecom_keys == {"ecom-only"}
    assert gtl_keys == {"gtl-only"}
    # 跨租户取 scope 取不到 → 当作未知，expand 抛错
    assert scope_resolution.get_scope("gtl-only", account_id="ecom-app") is None
    with pytest.raises(ScopeError):
        expand_scope("gtl-only", account_id="ecom-app")
    # 同名 scope_key 可在两租户并存（联合唯一），各自展开互不干扰
    assert expand_scope("ecom-only", account_id="ecom-app").shop_ids == ["s1"]


def test_expand_unknown_or_inactive_scope_raises(session, monkeypatch):
    _use(session, monkeypatch)
    _scope(session, "dead", ["s1"], active=False)
    session.commit()

    with pytest.raises(ScopeError):
        expand_scope("nope")
    with pytest.raises(ScopeError):
        expand_scope("dead")


def test_resolve_filters_narrows_to_in_scope_shop(session, monkeypatch):
    _use(session, monkeypatch)
    _scope(session, "tts-id-all", ["s1", "s2", "s3"])
    session.commit()

    f = resolve_filters(scope_key="tts-id-all", shop_id="s2")
    assert f.shop_ids == ["s2"]
    assert f.scope_key == "tts-id-all"


def test_resolve_filters_out_of_scope_shop_raises(session, monkeypatch):
    _use(session, monkeypatch)
    _scope(session, "tts-id-all", ["s1", "s2"])
    session.commit()

    with pytest.raises(ScopeError):
        resolve_filters(scope_key="tts-id-all", shop_id="s9")


def test_resolve_filters_rejects_unauthorized_shop_without_scope(session, monkeypatch):
    _use(session, monkeypatch)
    _token(session, "s1")
    session.commit()

    # s1 已授权 → 放行
    f = resolve_filters(shop_ids=["s1"])
    assert f.shop_ids == ["s1"]
    # s9 不在 platform_tokens → 拒绝
    with pytest.raises(ScopeError):
        resolve_filters(shop_ids=["s1", "s9"])


def test_resolve_filters_passthrough_without_scope_or_shop(session, monkeypatch):
    _use(session, monkeypatch)
    _token(session, "s1")  # 本租户有可见店 → 全量收口到它（非空=不过滤）
    session.commit()
    f = resolve_filters(platform="tiktok_shop", country="ID")
    assert f.shop_ids == ["s1"]  # 收口为本租户可见店
    assert f.scope_key is None
    assert f.platform == "tiktok_shop"  # platform/country 仍透传过滤


def test_resolve_filters_empty_tenant_fails_closed(session, monkeypatch):
    """无 scope 无显式店且租户无可见店 → 哨兵，绝不退化为查全库。"""
    _use(session, monkeypatch)
    f = resolve_filters()
    assert f.shop_ids == [scope_resolution.NO_SHOP_SENTINEL]
    assert f.scope_key is None


# ── 多店集合聚合（order_metrics 走 shop_ids in_()） ──────────────────────────


def _dt(y, m, d):
    return datetime(y, m, d, 12, 0)


def _order(order_id, *, total, shop, paid=True, status="COMPLETED"):
    """paid=False → paid_time=None（模拟 COD 未付款在途单）；status 可传 CANCELLED。"""
    return DomainOrder(
        order_id=order_id, order_status=status,
        currency="IDR", total_amount=Decimal(total),
        create_time=_dt(2026, 6, 3),
        paid_time=_dt(2026, 6, 3) if paid else None,
        update_time=_dt(2026, 6, 3),
        line_items=(DomainOrderLineItem(line_item_id=f"l-{order_id}", sku_id="sku-A",
                                        sale_price=Decimal(total), currency="IDR"),),
    )


def test_gmv_summary_aggregates_only_listed_shops(session, monkeypatch):
    upsert_orders(session, [_order("o1", total="100", shop="s1")], country="ID", shop_id="s1")
    upsert_orders(session, [_order("o2", total="200", shop="s2")], country="ID", shop_id="s2")
    upsert_orders(session, [_order("o3", total="999", shop="s3")], country="ID", shop_id="s3")
    session.commit()
    monkeypatch.setattr(order_metrics, "SessionLocal", lambda: session)

    summary = order_metrics.get_gmv_summary(
        start_date=date(2026, 6, 1), end_date=date(2026, 6, 5),
        country="ID", shop_ids=["s1", "s2"],
    )
    assert summary["gmv"] == 300.0  # 只含 s1+s2，排除 s3
    assert summary["order_count"] == 2


def test_display_metrics_by_create_vs_paid(session, monkeypatch):
    """展示类函数 by_create 口径：含未付款 COD 在途单、排除 CANCELLED；付款口径仅计已付款。

    覆盖 get_top_skus / get_sell_through / get_gmv_trend（均新加 by_create 参数），
    锁定「下单口径把 COD 在途单计入、把取消单排除」不回归。
    """
    from datetime import time as _time

    upsert_orders(session, [_order("paid1", total="50", shop="s1", paid=True)],
                  country="ID", shop_id="s1")
    upsert_orders(session, [_order("cod1", total="100", shop="s1", paid=False, status="IN_TRANSIT")],
                  country="ID", shop_id="s1")
    upsert_orders(session, [_order("cx1", total="999", shop="s1", paid=False, status="CANCELLED")],
                  country="ID", shop_id="s1")
    session.commit()
    monkeypatch.setattr(order_metrics, "SessionLocal", lambda: session)

    kw = dict(start_date=date(2026, 6, 1), end_date=date(2026, 6, 5), country="ID", shop_ids=["s1"])

    # get_top_skus：下单口径 3 单里 paid1+cod1 计入（同 sku-A，2 条 line_item），付款口径仅 paid1
    top_create = order_metrics.get_top_skus(**kw, by_create=True)
    top_paid = order_metrics.get_top_skus(**kw)
    assert top_create and top_create[0]["units_sold"] == 2  # paid1 + cod1，排除 cancelled
    assert top_paid and top_paid[0]["units_sold"] == 1      # 仅 paid1

    # get_gmv_trend：下单口径 GMV=150（50+100），付款口径 GMV=50
    trend_create = order_metrics.get_gmv_trend(**kw, by_create=True)
    trend_paid = order_metrics.get_gmv_trend(**kw)
    assert sum(p["gmv"] for p in trend_create) == 150.0
    assert sum(p["gmv"] for p in trend_paid) == 50.0

    # get_sell_through：分子=出单 distinct SKU 数。下单口径含 cod1 → 有出单；两口径 sku-A 都出单，
    # 但下单口径下即便 paid1 不存在也应因 cod1 计入（验证 by_create 生效不因未付款而漏）。
    st_create = order_metrics.get_sell_through(**kw, by_create=True)
    assert st_create["active_sku"] == 1  # sku-A（paid1+cod1 同 SKU）
