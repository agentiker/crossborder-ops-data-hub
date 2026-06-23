"""爆单提醒 metrics + decision + 当日去重单测。

get_units_by_product：按商品聚合已付款销量。
build_decision：破阈报、未破不报、当日已报不复读、跨天重置。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from models.base_models import HotsellAlertState, OrderHeader, OrderLineItem
from services import hotsell_alerts
from services.metrics_store import (
    get_hotsell_reported_ids,
    upsert_hotsell_alert_state,
)
from services.order_metrics import get_units_by_product


# ── metrics ──────────────────────────────────────────────────────────────────

def _order(session, oid, paid=datetime(2026, 6, 1, 12, 0)):
    session.add(OrderHeader(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        order_id=oid, idempotency_key=f"ik-{oid}", total_amount=Decimal("100"),
        currency="IDR", paid_time=paid,
    ))


def _line(session, lid, oid, pid, name="商品A"):
    session.add(OrderLineItem(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        idempotency_key=f"li-{lid}", line_item_id=lid, order_id=oid,
        product_id=pid, product_name=name, sku_id=f"sku-{pid}",
    ))


def test_units_by_product_aggregates(session):
    _order(session, "o1")
    _order(session, "o2")
    # 商品 p1 三件（跨两单），p2 一件
    _line(session, "l1", "o1", "p1", "连衣裙")
    _line(session, "l2", "o1", "p1", "连衣裙")
    _line(session, "l3", "o2", "p1", "连衣裙")
    _line(session, "l4", "o2", "p2", "短袖")
    session.commit()

    res = get_units_by_product(
        start_date=date(2026, 6, 1), end_date=date(2026, 6, 1),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"], session=session,
    )
    assert res["p1"]["units"] == 3
    assert res["p1"]["product_name"] == "连衣裙"
    assert res["p2"]["units"] == 1


# ── decision ─────────────────────────────────────────────────────────────────

_KW = dict(threshold=50, scope_display="印尼测试店", date_label="6/1")


def _units(**pid_units):
    return {pid: {"units": u, "product_name": f"商品{pid}"} for pid, u in pid_units.items()}


def test_decision_alerts_on_cross():
    d = hotsell_alerts.build_decision(
        units_by_product=_units(p1=60, p2=10), prev_reported_ids=[], **_KW,
    )
    assert d.should_alert is True
    assert [p["product_id"] for p in d.new_products] == ["p1"]
    assert d.new_reported_ids == ["p1"]
    assert "爆单提醒" in d.message and "p1" in d.message


def test_decision_no_alert_below_threshold():
    d = hotsell_alerts.build_decision(
        units_by_product=_units(p1=49), prev_reported_ids=[], **_KW,
    )
    assert d.should_alert is False
    assert d.new_reported_ids == []


def test_decision_dedup_same_day():
    """p1 已在今天报过 → 不复读；p3 新破阈 → 报。"""
    d = hotsell_alerts.build_decision(
        units_by_product=_units(p1=80, p3=55), prev_reported_ids=["p1"], **_KW,
    )
    assert d.should_alert is True
    assert [p["product_id"] for p in d.new_products] == ["p3"]
    assert d.new_reported_ids == ["p1", "p3"]  # 写回当日全部破阈


def test_decision_sorted_by_units_desc():
    d = hotsell_alerts.build_decision(
        units_by_product=_units(p1=55, p2=99), prev_reported_ids=[], **_KW,
    )
    assert [p["product_id"] for p in d.new_products] == ["p2", "p1"]


# ── 当日去重 state 跨天重置 ──────────────────────────────────────────────────

def test_reported_ids_resets_across_days(session):
    upsert_hotsell_alert_state(
        session, alert_type=hotsell_alerts.ALERT_TYPE, account_id="a", scope_key=None,
        report_date=date(2026, 6, 1), reported_product_ids=["p1", "p2"], mark_sent=True,
    )
    session.commit()
    state = session.query(HotsellAlertState).one()
    # 同一天读出已报集合
    assert get_hotsell_reported_ids(state, date(2026, 6, 1)) == ["p1", "p2"]
    # 跨天读 → 空（新的一天重新计）
    assert get_hotsell_reported_ids(state, date(2026, 6, 2)) == []
