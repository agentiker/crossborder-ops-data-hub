"""扣点率告警 metrics + decision 单测。

get_settled_fee_rate：只纳入已结算订单(有 FT 行)、GMV 不重复计数(一单多交易)、按 currency 分组、
费率=Σ扣费/ΣGMV。
build_decision：升超双阈值报、降不报、未达阈值不报、低GMV/基准不足/基准0 优雅跳过、多币种取主力盘。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from models.base_models import FactFinanceTransaction, OrderHeader
from services import fee_rate_alerts
from services.fee_rate_metrics import get_settled_fee_rate


# ── metrics ──────────────────────────────────────────────────────────────────

def _order(session, oid, *, gmv, currency="IDR", paid=datetime(2026, 6, 1, 12, 0)):
    session.add(OrderHeader(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        order_id=oid, idempotency_key=f"ik-{oid}", total_amount=Decimal(str(gmv)),
        currency=currency, paid_time=paid,
    ))


def _ft(session, tid, oid, *, fee_tax, currency="IDR", commission="0", md=date(2026, 6, 1)):
    # fee_breakdown 存原始 API 负数(扣款)；components 从此 JSON 聚合（取绝对值）
    fb = {}
    if Decimal(str(commission)) != 0:
        fb["platform_commission_amount"] = str(-Decimal(str(commission)))
    session.add(FactFinanceTransaction(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        scope_key=f"sk-{tid}", transaction_id=tid, order_id=oid,
        metric_date=md, currency=currency,
        fee_tax_amount=Decimal(str(fee_tax)),
        platform_commission_amount=Decimal(str(commission)),
        fee_breakdown=fb,
    ))


def test_settled_rate_excludes_unsettled_and_computes(session):
    """已结算订单(有FT)纳入；无FT的订单贡献GMV但不纳入(避免虚低)。"""
    _order(session, "o1", gmv=1000)
    _order(session, "o2", gmv=1000)
    _order(session, "o3", gmv=5000)  # 无 FT → 未结算，不纳入
    _ft(session, "t1", "o1", fee_tax=200, commission=150)
    _ft(session, "t2", "o2", fee_tax=300, commission=250)
    session.commit()

    res = get_settled_fee_rate(
        start_date=date(2026, 6, 1), end_date=date(2026, 6, 1),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"], session=session,
    )
    assert set(res.keys()) == {"IDR"}
    idr = res["IDR"]
    assert idr["order_count"] == 2  # o3 未结算被剔除
    assert idr["gmv"] == 2000.0
    assert idr["total_fee"] == 500.0
    assert idr["rate"] == 0.25  # 500/2000
    assert idr["components"]["platform_commission_amount"] == 400.0


def test_settled_rate_gmv_not_double_counted(session):
    """一单多笔结算交易：GMV 只算一次，扣费各交易求和。"""
    _order(session, "o1", gmv=1000)
    _ft(session, "t1", "o1", fee_tax=200)
    _ft(session, "t2", "o1", fee_tax=50)  # 同一单第二笔交易（如调整）
    session.commit()

    res = get_settled_fee_rate(
        start_date=date(2026, 6, 1), end_date=date(2026, 6, 1),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"], session=session,
    )
    assert res["IDR"]["gmv"] == 1000.0  # 不是 2000
    assert res["IDR"]["total_fee"] == 250.0
    assert res["IDR"]["rate"] == 0.25


def test_settled_rate_groups_by_currency(session):
    """不同币种各自分组，不混算。"""
    _order(session, "o1", gmv=1000, currency="IDR")
    _order(session, "o2", gmv=100, currency="USD")
    _ft(session, "t1", "o1", fee_tax=200, currency="IDR")
    _ft(session, "t2", "o2", fee_tax=30, currency="USD")
    session.commit()

    res = get_settled_fee_rate(
        start_date=date(2026, 6, 1), end_date=date(2026, 6, 1),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"], session=session,
    )
    assert res["IDR"]["rate"] == 0.2
    assert res["USD"]["rate"] == 0.3


def test_settled_rate_empty_when_no_orders(session):
    res = get_settled_fee_rate(
        start_date=date(2026, 6, 1), end_date=date(2026, 6, 1),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"], session=session,
    )
    assert res == {}


# ── decision ─────────────────────────────────────────────────────────────────

def _ccy(rate, gmv, *, components=None):
    return {"IDR": {
        "currency": "IDR", "rate": rate, "gmv": gmv, "total_fee": rate * gmv,
        "order_count": 100, "components": components or {},
    }}


_KW = dict(
    scope_display="印尼测试店", min_gmv=1000.0, rel_pct=0.15, abs_pct=0.03,
    eval_window_label="6/1~6/7", baseline_window_label="5/4~5/31",
)


def test_decision_alerts_on_rise_over_thresholds():
    """费率 20%→28%：相对 +40% > 15%、绝对 +8pct > 3pct → 报。"""
    d = fee_rate_alerts.build_decision(
        eval_by_ccy=_ccy(0.28, 50000, components={"platform_commission_amount": 12000.0}),
        baseline_by_ccy=_ccy(0.20, 50000), **_KW,
    )
    assert d.should_alert is True
    assert d.currency == "IDR"
    assert d.message and "扣点率异常升高" in d.message
    assert abs(d.abs_change - 0.08) < 1e-9
    assert d.evidence
    assert d.evidence["source"] == "tiktok_finance_api"
    assert d.evidence["mode"] == "current_components"
    assert d.evidence["fee_items"][0]["source_field"] == "fee_tax_breakdown.fee.platform_commission_amount"
    assert "检测依据" in d.message
    assert "fee_tax_breakdown" not in d.message


def test_decision_evidence_uses_attribution_when_components_overlap():
    """两侧有同名费项且涨幅过归因阈值 → evidence 标注具体升幅来源。"""
    d = fee_rate_alerts.build_decision(
        eval_by_ccy=_ccy(0.28, 50000, components={"platform_commission_amount": 14000.0}),
        baseline_by_ccy=_ccy(0.20, 50000, components={"platform_commission_amount": 10000.0}),
        **_KW,
    )
    assert d.should_alert is True
    item = d.evidence["fee_items"][0]
    assert d.evidence["mode"] == "attribution"
    assert item["key"] == "platform_commission_amount"
    assert round(item["from"], 4) == 0.2
    assert round(item["to"], 4) == 0.28
    assert round(item["delta"], 4) == 0.08


def test_decision_no_alert_on_drop():
    """费率下降不报（对卖家是好事）。"""
    d = fee_rate_alerts.build_decision(
        eval_by_ccy=_ccy(0.15, 50000), baseline_by_ccy=_ccy(0.20, 50000), **_KW,
    )
    assert d.should_alert is False
    assert d.skip_reason


def test_decision_no_alert_below_thresholds():
    """升幅小：20%→21%，相对 +5% < 15% → 不报。"""
    d = fee_rate_alerts.build_decision(
        eval_by_ccy=_ccy(0.21, 50000), baseline_by_ccy=_ccy(0.20, 50000), **_KW,
    )
    assert d.should_alert is False


def test_decision_abs_guard_blocks_tiny_base():
    """相对升幅大但绝对小：1%→1.5%，相对+50%过，但绝对 +0.5pct < 3pct → 不报。"""
    d = fee_rate_alerts.build_decision(
        eval_by_ccy=_ccy(0.015, 50000), baseline_by_ccy=_ccy(0.01, 50000), **_KW,
    )
    assert d.should_alert is False


def test_decision_skips_low_eval_gmv():
    d = fee_rate_alerts.build_decision(
        eval_by_ccy=_ccy(0.30, 500), baseline_by_ccy=_ccy(0.20, 50000), **_KW,
    )
    assert d.should_alert is False
    assert "护栏" in d.skip_reason


def test_decision_skips_insufficient_baseline():
    d = fee_rate_alerts.build_decision(
        eval_by_ccy=_ccy(0.30, 50000), baseline_by_ccy=_ccy(0.20, 500), **_KW,
    )
    assert d.should_alert is False
    assert "历史" in d.skip_reason


def test_decision_skips_empty_eval():
    d = fee_rate_alerts.build_decision(
        eval_by_ccy={}, baseline_by_ccy=_ccy(0.20, 50000), **_KW,
    )
    assert d.should_alert is False


def test_decision_picks_dominant_currency():
    """多币种取评估窗口 GMV 最大的币种为主币种。"""
    eval_by = {
        "IDR": {"currency": "IDR", "rate": 0.30, "gmv": 100000, "total_fee": 30000,
                "order_count": 50, "components": {}},
        "USD": {"currency": "USD", "rate": 0.10, "gmv": 1000, "total_fee": 100,
                "order_count": 2, "components": {}},
    }
    base_by = {
        "IDR": {"currency": "IDR", "rate": 0.20, "gmv": 100000, "total_fee": 20000,
                "order_count": 50, "components": {}},
        "USD": {"currency": "USD", "rate": 0.10, "gmv": 1000, "total_fee": 100,
                "order_count": 2, "components": {}},
    }
    d = fee_rate_alerts.build_decision(eval_by_ccy=eval_by, baseline_by_ccy=base_by, **_KW)
    assert d.currency == "IDR"
    assert d.should_alert is True
