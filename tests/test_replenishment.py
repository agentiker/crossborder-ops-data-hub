"""补货公式 service 单测：目标=销量×系数、扣库存+在途、≤0剔除、超级爆品×系数、降序、变体回填。"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

from core.timezone import business_today
from models.base_models import Inventory, OrderHeader, OrderLineItem, SkuVariant
from services.replenishment import compute_replenishment
from services.replenishment_config import set_super_hot, upsert_config


def _paid_dt():
    """窗口内某个完整业务日的 paid_time（昨天再往前几天，noon UTC 稳归当日）。"""
    d = business_today() - timedelta(days=5)
    return datetime.combine(d, time(12, 0))


def _sell(session, sku_id, product_id, units):
    """造 units 件已付款销量（units 个 line_item，跨若干订单）。"""
    for i in range(units):
        oid = f"o-{sku_id}-{i}"
        session.add(OrderHeader(
            platform="tiktok_shop", country="ID", shop_id="shop-1",
            order_id=oid, idempotency_key=f"ik-{oid}", total_amount=Decimal("10"),
            currency="IDR", paid_time=_paid_dt(),
        ))
        session.add(OrderLineItem(
            platform="tiktok_shop", country="ID", shop_id="shop-1",
            idempotency_key=f"li-{sku_id}-{i}", line_item_id=f"l-{sku_id}-{i}",
            order_id=oid, sku_id=sku_id, product_id=product_id, product_name=f"商品{product_id}",
        ))


def _stock(session, sku_id, product_id, available):
    session.add(Inventory(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        idempotency_key=f"inv-{sku_id}", sku_id=sku_id, product_id=product_id,
        available_stock=available,
    ))


def _variant(session, sku_id, product_id, *, color="Red", size="M"):
    session.add(SkuVariant(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        idempotency_key=f"var-{sku_id}", sku_id=sku_id, product_id=product_id,
        seller_sku=f"SS-{sku_id}", product_name=f"商品{product_id}", color=color, size=size,
    ))


_SCOPE = dict(platform="tiktok_shop", country="ID", shop_ids=["shop-1"])


def test_basic_formula_and_sort(session):
    # s1: 30 销量 × 1.5 = 45，库存 10 → 补 35
    _sell(session, "s1", "p1", 30); _stock(session, "s1", "p1", 10); _variant(session, "s1", "p1")
    # s3: 5 × 1.5 = ceil 8，库存 100 → ≤0 剔除
    _sell(session, "s3", "p3", 5); _stock(session, "s3", "p3", 100); _variant(session, "s3", "p3")
    session.commit()

    rows = compute_replenishment(account_id="ecom-app", session=session, **_SCOPE)
    assert [r["sku_id"] for r in rows] == ["s1"]  # s3 剔除
    r = rows[0]
    assert r["units"] == 30
    assert r["target"] == 45
    assert r["available"] == 10
    assert r["replenish_qty"] == 35
    assert r["color"] == "Red" and r["size"] == "M"  # 变体回填
    assert r["seller_sku"] == "SS-s1"
    assert r["is_super_hot"] is False


def test_super_hot_multiplier(session):
    _sell(session, "s2", "p2", 10); _stock(session, "s2", "p2", 0); _variant(session, "s2", "p2")
    set_super_hot(session, product_id="p2", account_id="ecom-app")
    session.commit()

    rows = compute_replenishment(account_id="ecom-app", session=session, **_SCOPE)
    r = rows[0]
    assert r["is_super_hot"] is True
    assert r["multiplier"] == 2.0
    assert r["target"] == 20  # 10 × 2.0
    assert r["replenish_qty"] == 20


def test_intransit_reduces_qty(session):
    _sell(session, "s1", "p1", 30); _stock(session, "s1", "p1", 10); _variant(session, "s1", "p1")
    session.commit()
    # 在途 25 → 45 - 10 - 25 = 10
    rows = compute_replenishment(
        account_id="ecom-app", session=session, intransit_by_sku={"s1": 25}, **_SCOPE
    )
    assert rows[0]["replenish_qty"] == 10
    assert rows[0]["intransit"] == 25


def test_config_override_changes_target(session):
    _sell(session, "s1", "p1", 20); _stock(session, "s1", "p1", 0); _variant(session, "s1", "p1")
    upsert_config(session, account_id="ecom-app", scope_key=None, normal_multiplier=2.0)
    session.commit()
    rows = compute_replenishment(account_id="ecom-app", session=session, **_SCOPE)
    assert rows[0]["target"] == 40  # 20 × 2.0（覆盖默认 1.5）


def test_no_sales_returns_empty(session):
    _stock(session, "s1", "p1", 10); _variant(session, "s1", "p1")
    session.commit()
    assert compute_replenishment(account_id="ecom-app", session=session, **_SCOPE) == []


def test_missing_variant_still_replenishes(session):
    """无变体记录的 SKU 仍计补货，款号/颜色/尺码为 None，按普通系数。"""
    _sell(session, "sX", "pX", 30); _stock(session, "sX", "pX", 0)
    session.commit()
    rows = compute_replenishment(account_id="ecom-app", session=session, **_SCOPE)
    assert rows[0]["sku_id"] == "sX"
    assert rows[0]["color"] is None and rows[0]["product_name"] is None
    assert rows[0]["replenish_qty"] == 45
