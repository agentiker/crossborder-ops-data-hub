"""及时费率告警（unsettled 预估口径）metrics + decision 单测。

get_unsettled_fee_rate：取数源 FactUnsettledFee（预估额，按 metric_date 归日），扣费=Σestimated_fee_amount、
GMV=这些订单 distinct OrderHeader.total_amount（与 settled 同基准）、按 currency 分组、一单多笔不重复计 GMV。
build_decision(realtime=True)：文案标注"预估口径·实时"、附最新费率政策注脚。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from models.base_models import FactUnsettledFee, OrderHeader
from services import fee_rate_alerts
from services.fee_rate_metrics import get_unsettled_fee_rate


def _order(session, oid, *, gmv, currency="IDR", create=datetime(2026, 6, 24, 5, 0)):
    # 未结算订单多为 COD 在途/未付款，GMV 用 total_amount（与 settled 同基准），不依赖 paid_time
    session.add(OrderHeader(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        order_id=oid, idempotency_key=f"ik-{oid}", total_amount=Decimal(str(gmv)),
        currency=currency, order_status="IN_TRANSIT", create_time=create,
    ))


def _unsettled(session, tid, oid, *, fee, currency="IDR", affiliate="0", md=date(2026, 6, 24)):
    session.add(FactUnsettledFee(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        scope_key=f"u-{tid}", transaction_id=tid, order_id=oid,
        metric_date=md, currency=currency,
        estimated_fee_amount=Decimal(str(fee)),
        affiliate_commission=Decimal(str(affiliate)),
    ))


# ── metrics ──────────────────────────────────────────────────────────────────

def test_unsettled_rate_computes(session):
    """预估费率 = Σestimated_fee / Σ订单GMV，按 currency 分组，广告组件累加。"""
    _order(session, "o1", gmv=1000)
    _order(session, "o2", gmv=1000)
    _unsettled(session, "t1", "o1", fee=200, affiliate=20)
    _unsettled(session, "t2", "o2", fee=300, affiliate=30)
    session.commit()

    res = get_unsettled_fee_rate(
        start_date=date(2026, 6, 24), end_date=date(2026, 6, 24),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"], session=session,
    )
    assert set(res.keys()) == {"IDR"}
    idr = res["IDR"]
    assert idr["order_count"] == 2
    assert idr["gmv"] == 2000.0
    assert idr["total_fee"] == 500.0
    assert idr["rate"] == 0.25  # 500/2000
    assert idr["components"]["affiliate_commission"] == 50.0


def test_unsettled_rate_gmv_not_double_counted(session):
    """一单多笔未结算预估：GMV 只算一次，扣费各笔求和。"""
    _order(session, "o1", gmv=1000)
    _unsettled(session, "t1", "o1", fee=200)
    _unsettled(session, "t2", "o1", fee=50)  # 同一单第二笔
    session.commit()

    res = get_unsettled_fee_rate(
        start_date=date(2026, 6, 24), end_date=date(2026, 6, 24),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"], session=session,
    )
    assert res["IDR"]["gmv"] == 1000.0  # 不是 2000
    assert res["IDR"]["total_fee"] == 250.0
    assert res["IDR"]["rate"] == 0.25


def test_unsettled_rate_groups_by_currency(session):
    _order(session, "o1", gmv=1000, currency="IDR")
    _order(session, "o2", gmv=100, currency="USD")
    _unsettled(session, "t1", "o1", fee=200, currency="IDR")
    _unsettled(session, "t2", "o2", fee=30, currency="USD")
    session.commit()

    res = get_unsettled_fee_rate(
        start_date=date(2026, 6, 24), end_date=date(2026, 6, 24),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"], session=session,
    )
    assert res["IDR"]["rate"] == 0.2
    assert res["USD"]["rate"] == 0.3


def test_unsettled_rate_empty_when_none(session):
    res = get_unsettled_fee_rate(
        start_date=date(2026, 6, 24), end_date=date(2026, 6, 24),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"], session=session,
    )
    assert res == {}


def test_unsettled_rate_window_excludes_outside(session):
    """窗口外 metric_date 的预估行不纳入。"""
    _order(session, "o1", gmv=1000)
    _order(session, "o2", gmv=1000)
    _unsettled(session, "t1", "o1", fee=200, md=date(2026, 6, 24))
    _unsettled(session, "t2", "o2", fee=900, md=date(2026, 6, 20))  # 窗口外
    session.commit()

    res = get_unsettled_fee_rate(
        start_date=date(2026, 6, 24), end_date=date(2026, 6, 24),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"], session=session,
    )
    assert res["IDR"]["order_count"] == 1
    assert res["IDR"]["total_fee"] == 200.0


# ── decision（复用 build_decision，仅验 realtime 文案差异）─────────────────────

_KW = dict(
    scope_display="印尼测试店", min_gmv=1000.0, rel_pct=0.15, abs_pct=0.03,
    eval_window_label="6/22~6/24", baseline_window_label="5/13~6/9",
)


def _ccy(rate, gmv):
    return {"IDR": {"currency": "IDR", "rate": rate, "gmv": gmv, "total_fee": rate * gmv,
                    "order_count": 100, "components": {}}}


def test_realtime_decision_annotates_estimate():
    """realtime=True：预估口径文案 + 反映最新费率政策注脚（不带结算滞后注脚）。"""
    d = fee_rate_alerts.build_decision(
        eval_by_ccy=_ccy(0.28, 50000), baseline_by_ccy=_ccy(0.20, 50000), realtime=True, **_KW,
    )
    assert d.should_alert is True
    assert "预估口径·实时" in d.message
    assert "预估扣费率" in d.message
    assert "最新费率政策" in d.message
    assert "结算有滞后" not in d.message


def test_settled_decision_keeps_lag_footnote():
    """realtime 默认 False：保留结算滞后注脚（与及时口径区分）。"""
    d = fee_rate_alerts.build_decision(
        eval_by_ccy=_ccy(0.28, 50000), baseline_by_ccy=_ccy(0.20, 50000), **_KW,
    )
    assert "结算有滞后" in d.message
    assert "预估口径·实时" not in d.message
