"""退款/取消分析（services.refund_metrics）口径测试。

核心口径（见 docs/business-rules.md）：退款 = order_status=CANCELLED 且 paid_time 非空
（付款后取消 = 事实退款）；金额取 sub_total；发货前流失（未付款取消）不计入退款。
"""
from datetime import date, datetime
from decimal import Decimal

from ai_tools import operations_read  # noqa: F401  (ensures models imported)
from core.domain import DomainOrder, DomainOrderLineItem
from services import order_metrics, refund_metrics
from services.order_store import upsert_orders


def _dt(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi)


def _order(order_id, *, status, sub_total, paid, create=None, is_cod=False):
    """构造带 sub_total 的 DomainOrder（退款金额取 sub_total）。"""
    return DomainOrder(
        order_id=order_id,
        order_status=status,
        currency="IDR",
        total_amount=Decimal(sub_total),
        sub_total=Decimal(sub_total),
        create_time=create or paid or _dt(2026, 6, 3),
        paid_time=paid,
        is_cod=is_cod,
        line_items=(
            DomainOrderLineItem(
                line_item_id=f"{order_id}-l1", sku_id="sku-A",
                sale_price=Decimal(sub_total), currency="IDR",
            ),
        ),
    )


def _patch(session, monkeypatch):
    # refund_metrics 自身 + 它调用的 order_metrics.get_gmv_summary 都要指向测试 session
    monkeypatch.setattr(refund_metrics, "SessionLocal", lambda: session)
    monkeypatch.setattr(order_metrics, "SessionLocal", lambda: session)


def test_refund_summary_paid_cancelled_only(session, monkeypatch):
    """退款 = 付款后取消；未付款取消不计入；金额取 sub_total；退款率 = 退款额/展示GMV。"""
    orders = [
        # 正常完成单（进 GMV 分母，不是退款）
        _order("ok", status="COMPLETED", sub_total="100000", paid=_dt(2026, 6, 3)),
        # 付款后取消（事实退款）：50000
        _order("refund1", status="CANCELLED", sub_total="50000", paid=_dt(2026, 6, 3)),
        # 未付款取消（发货前流失，不是退款）
        _order("lost1", status="CANCELLED", sub_total="30000", paid=None,
               create=_dt(2026, 6, 3), is_cod=True),
    ]
    upsert_orders(session, orders, country="ID", shop_id="shop-1")
    session.commit()
    _patch(session, monkeypatch)

    s = refund_metrics.get_refund_summary(
        start_date=date(2026, 6, 3), end_date=date(2026, 6, 3),
        country="ID", shop_id="shop-1",
    )
    # 退款 = 仅付款后取消 refund1
    assert s["refund_order_count"] == 1
    assert s["refund_amount"] == 50000.0
    # 取消构成：2 单取消，1 付款后 + 1 未付款；COD 1 单（lost1）
    assert s["cancelled_total"] == 2
    assert s["paid_cancelled"] == 1
    assert s["unpaid_cancelled"] == 1
    assert s["cod_cancelled"] == 1
    # 展示 GMV = 含取消所有单 sub_total（100000+50000+30000=180000）；退款率=50000/180000
    assert s["gmv"] == 180000.0
    assert s["refund_rate"] == round(50000 / 180000, 4)


def test_refund_summary_no_refund(session, monkeypatch):
    """无付款后取消时退款为 0、率为 0（GMV 非 0）。"""
    upsert_orders(
        session,
        [_order("ok", status="COMPLETED", sub_total="100000", paid=_dt(2026, 6, 3))],
        country="ID", shop_id="shop-1",
    )
    session.commit()
    _patch(session, monkeypatch)

    s = refund_metrics.get_refund_summary(
        start_date=date(2026, 6, 3), end_date=date(2026, 6, 3),
        country="ID", shop_id="shop-1",
    )
    assert s["refund_amount"] == 0.0
    assert s["refund_order_count"] == 0
    assert s["cancelled_total"] == 0
    assert s["refund_rate"] == 0.0  # 50000/... 无退款 → 0/GMV = 0


def test_refund_trend_fills_and_dates_by_create(session, monkeypatch):
    """退款趋势按 create_time 归印尼日、空日补 0；只含付款后取消。"""
    orders = [
        _order("r1", status="CANCELLED", sub_total="20000", paid=_dt(2026, 6, 3),
               create=_dt(2026, 6, 3)),
        _order("r2", status="CANCELLED", sub_total="10000", paid=_dt(2026, 6, 5),
               create=_dt(2026, 6, 5)),
        # 未付款取消不进趋势
        _order("lost", status="CANCELLED", sub_total="99999", paid=None,
               create=_dt(2026, 6, 4)),
    ]
    upsert_orders(session, orders, country="ID", shop_id="shop-1")
    session.commit()
    _patch(session, monkeypatch)

    pts = refund_metrics.get_refund_trend(
        start_date=date(2026, 6, 3), end_date=date(2026, 6, 5),
        country="ID", shop_id="shop-1",
    )
    by = {p["date"]: p for p in pts}
    assert [p["date"] for p in pts] == ["2026-06-03", "2026-06-04", "2026-06-05"]
    assert by["2026-06-03"]["refund_amount"] == 20000.0
    assert by["2026-06-03"]["refund_order_count"] == 1
    # 6/4 只有未付款取消 → 退款趋势补 0
    assert by["2026-06-04"] == {"date": "2026-06-04", "refund_amount": 0.0, "refund_order_count": 0}
    assert by["2026-06-05"]["refund_amount"] == 10000.0


def test_refund_scope_isolation(session, monkeypatch):
    """按 shop 隔离：只统计目标店的退款。"""
    upsert_orders(
        session,
        [_order("a", status="CANCELLED", sub_total="50000", paid=_dt(2026, 6, 3))],
        country="ID", shop_id="shop-1",
    )
    upsert_orders(
        session,
        [_order("b", status="CANCELLED", sub_total="70000", paid=_dt(2026, 6, 3))],
        country="ID", shop_id="shop-2",
    )
    session.commit()
    _patch(session, monkeypatch)

    s1 = refund_metrics.get_refund_summary(
        start_date=date(2026, 6, 3), end_date=date(2026, 6, 3),
        country="ID", shop_id="shop-1",
    )
    assert s1["refund_amount"] == 50000.0 and s1["refund_order_count"] == 1
