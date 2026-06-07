from datetime import date, datetime, timezone

import pytest

from models.base_models import BusinessScope, PlatformToken
from platforms.tiktok_shop.schemas import OrderSchema
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
    f = resolve_filters(platform="tiktok_shop", country="ID")
    assert f.shop_ids == []
    assert f.scope_key is None
    assert f.platform == "tiktok_shop"


# ── 多店集合聚合（order_metrics 走 shop_ids in_()） ──────────────────────────


def _unix(y, m, d):
    return int(datetime(y, m, d, 12, 0, tzinfo=timezone.utc).timestamp())


def _order(order_id, *, total, shop):
    return OrderSchema.model_validate({
        "id": order_id, "status": "COMPLETED",
        "create_time": _unix(2026, 6, 3), "paid_time": _unix(2026, 6, 3),
        "update_time": _unix(2026, 6, 3),
        "payment": {"currency": "IDR", "total_amount": total},
        "line_items": [{"id": f"l-{order_id}", "sku_id": "sku-A", "sale_price": total}],
    })


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
