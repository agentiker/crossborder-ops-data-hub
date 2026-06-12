from datetime import datetime
from decimal import Decimal

from core.domain import DomainOrder, DomainOrderLineItem
from models.base_models import PendingFulfillment
from services.fulfillment_store import replace_pending_fulfillments


def _dt(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi)


def _order(order_id, *, sla=None, lines=None, status="AWAITING_SHIPMENT", total="100000"):
    """构造待发货 DomainOrder（store 只认中立 DTO）。"""
    lines = lines or []
    return DomainOrder(
        order_id=order_id,
        order_status=status,
        currency="IDR",
        total_amount=Decimal(total),
        tts_sla_time=sla,
        delivery_option_name="Standard",
        line_items=tuple(
            DomainOrderLineItem(
                line_item_id=ln["id"],
                sku_id=ln.get("sku_id"),
                product_name=ln.get("product_name"),
                currency="IDR",
            )
            for ln in lines
        ),
    )


def test_replace_idempotent(session):
    order = _order("o1", sla=_dt(2026, 6, 13), lines=[{"id": "l1", "sku_id": "A"}])
    assert replace_pending_fulfillments(session, [order], country="ID", shop_id="shop-1") == (1, 0)
    # 同批重跑：upsert 1、删 0、表内仍 1 行
    assert replace_pending_fulfillments(session, [order], country="ID", shop_id="shop-1") == (1, 0)
    session.commit()
    assert session.query(PendingFulfillment).count() == 1


def test_snapshot_removes_ghost(session):
    """第一次 2 单待发货，第二次只剩 1 单（另一单已发走）→ 表里恰 1 行，且为保留的那单。"""
    first = [
        _order("o1", sla=_dt(2026, 6, 13), lines=[{"id": "l1", "sku_id": "A"}]),
        _order("o2", sla=_dt(2026, 6, 14), lines=[{"id": "l2", "sku_id": "B"}]),
    ]
    replace_pending_fulfillments(session, first, country="ID", shop_id="shop-1")
    session.commit()
    assert session.query(PendingFulfillment).count() == 2

    # o2 已发货，本次快照只含 o1
    upserted, removed = replace_pending_fulfillments(
        session, [first[0]], country="ID", shop_id="shop-1"
    )
    session.commit()
    assert (upserted, removed) == (1, 1)
    rows = session.query(PendingFulfillment).all()
    assert [r.order_id for r in rows] == ["o1"]


def test_empty_snapshot_clears_shop(session):
    """空快照（0 单待发货）应清空本店全部旧行。"""
    replace_pending_fulfillments(
        session,
        [_order("o1", sla=_dt(2026, 6, 13), lines=[{"id": "l1"}])],
        country="ID",
        shop_id="shop-1",
    )
    session.commit()
    assert session.query(PendingFulfillment).count() == 1

    upserted, removed = replace_pending_fulfillments(session, [], country="ID", shop_id="shop-1")
    session.commit()
    assert (upserted, removed) == (0, 1)
    assert session.query(PendingFulfillment).count() == 0


def test_scope_isolation(session):
    """刷 shop-1（清空）不应误删 shop-2 的待发货行。"""
    replace_pending_fulfillments(
        session, [_order("a1", sla=_dt(2026, 6, 13), lines=[{"id": "la"}])],
        country="ID", shop_id="shop-1",
    )
    replace_pending_fulfillments(
        session, [_order("b1", sla=_dt(2026, 6, 13), lines=[{"id": "lb"}])],
        country="ID", shop_id="shop-2",
    )
    session.commit()
    assert session.query(PendingFulfillment).count() == 2

    # 清空 shop-1，shop-2 应原封不动
    replace_pending_fulfillments(session, [], country="ID", shop_id="shop-1")
    session.commit()
    rows = session.query(PendingFulfillment).all()
    assert [r.shop_id for r in rows] == ["shop-2"]


def test_fields_updated_on_upsert(session):
    """同 order_id 再次出现时，SLA/status 等业务列应被刷新。"""
    replace_pending_fulfillments(
        session, [_order("o1", status="AWAITING_SHIPMENT", sla=_dt(2026, 6, 13), lines=[{"id": "l1"}])],
        country="ID", shop_id="shop-1",
    )
    session.commit()
    replace_pending_fulfillments(
        session, [_order("o1", status="AWAITING_COLLECTION", sla=_dt(2026, 6, 15), lines=[{"id": "l1"}])],
        country="ID", shop_id="shop-1",
    )
    session.commit()
    row = session.query(PendingFulfillment).one()
    assert row.order_status == "AWAITING_COLLECTION"
    assert row.tts_sla_time == _dt(2026, 6, 15)


def test_item_count_and_first_product_name(session):
    order = _order(
        "o1",
        sla=_dt(2026, 6, 13),
        lines=[
            {"id": "l1", "sku_id": "A", "product_name": "首件商品"},
            {"id": "l2", "sku_id": "B", "product_name": "第二件"},
        ],
    )
    replace_pending_fulfillments(session, [order], country="ID", shop_id="shop-1")
    session.commit()
    row = session.query(PendingFulfillment).one()
    assert row.item_count == 2
    assert row.first_product_name == "首件商品"
