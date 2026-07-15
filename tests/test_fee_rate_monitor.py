"""看板「费率监控」卡 get_fee_rate_monitor 单测（实时算、复用 B1 及时口径 + build_decision）。

三态：normal（有数据未达阈值）/ alert（异常升高，附结构化分项归因）/ insufficient（数据不足不误报）。
口径与 flows/scan_fulfillment_alerts._scan_unsettled_fee_rate 一致：eval=unsettled 预估、baseline=settled 历史。
窗口依赖 business_today()，测试 monkeypatch 成固定 today 以确定性构造两窗口数据。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from models.base_models import FactFinanceTransaction, FactUnsettledFee, OrderHeader
from services import fee_rate_metrics
from services.fee_rate_metrics import get_fee_rate_monitor

TODAY = date(2026, 6, 24)
# 默认参数：realtime_eval_days=3 → eval [6/22, 6/24]；settle_lag=14 → baseline_end 6/10，
# baseline_days=28 → baseline_start 5/14。下面的数据落在这两个窗口内。
EVAL_MD = date(2026, 6, 23)
BASE_MD = date(2026, 5, 20)
BASE_PAID = datetime(2026, 5, 20, 12, 0)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr(fee_rate_metrics, "business_today", lambda: TODAY)


def _unsettled_order(session, oid, *, gmv, fee, comp_key=None, comp_amt=0, currency="IDR", md=EVAL_MD):
    session.add(OrderHeader(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        order_id=oid, idempotency_key=f"ik-{oid}", total_amount=Decimal(str(gmv)),
        currency=currency, order_status="IN_TRANSIT", create_time=datetime(2026, 6, 23, 5, 0),
    ))
    fb = {comp_key: str(-Decimal(str(comp_amt)))} if comp_key else {}
    session.add(FactUnsettledFee(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        scope_key=f"u-{oid}", transaction_id=f"u{oid}", order_id=oid,
        metric_date=md, currency=currency,
        estimated_fee_amount=Decimal(str(fee)), fee_breakdown=fb,
    ))


def _settled_order(session, oid, *, gmv, fee, comp_key=None, comp_amt=0, currency="IDR", md=BASE_MD):
    session.add(OrderHeader(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        order_id=oid, idempotency_key=f"ik-{oid}", total_amount=Decimal(str(gmv)),
        currency=currency, paid_time=BASE_PAID,
    ))
    fb = {comp_key: str(-Decimal(str(comp_amt)))} if comp_key else {}
    session.add(FactFinanceTransaction(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        scope_key=f"s-{oid}", transaction_id=f"s{oid}", order_id=oid,
        metric_date=md, currency=currency,
        fee_tax_amount=Decimal(str(fee)), fee_breakdown=fb,
    ))


def _scope():
    return dict(platform="tiktok_shop", country="ID", shop_ids=["shop-1"])


def test_monitor_normal(session):
    """eval 费率 ≈ baseline（升幅未过阈值）→ status=normal，有当前构成。"""
    # baseline 12M GMV，费率 20%
    _settled_order(session, "s1", gmv=6_000_000, fee=1_200_000)
    _settled_order(session, "s2", gmv=6_000_000, fee=1_200_000)
    # eval 12M GMV，费率 20.5%（abs +0.5pct < 3pct 阈值 → 正常）
    _unsettled_order(session, "u1", gmv=6_000_000, fee=1_230_000)
    _unsettled_order(session, "u2", gmv=6_000_000, fee=1_230_000)
    session.commit()

    res = get_fee_rate_monitor(session=session, trend_days=3, **_scope())
    assert res["status"] == "normal"
    assert res["currency"] == "IDR"
    assert round(res["current_rate"], 4) == 0.205
    assert round(res["baseline_rate"], 4) == 0.20
    assert res["attributions"] == []  # 正常态不归因
    assert len(res["trend"]) == 3
    # 趋势点带 complete 标志：trend_days=3 都在近 settle_lag(14) 天内 → 结算未完成 → complete=False
    assert all("complete" in p for p in res["trend"])
    assert all(p["complete"] is False for p in res["trend"])


def test_monitor_alert_with_attribution(session):
    """eval 费率远高于 baseline（过双阈值）→ status=alert，同名费项归因点名。"""
    # baseline 20%，含 platform_commission 占比 12%
    _settled_order(session, "s1", gmv=6_000_000, fee=1_200_000,
                   comp_key="platform_commission_amount", comp_amt=720_000)
    _settled_order(session, "s2", gmv=6_000_000, fee=1_200_000,
                   comp_key="platform_commission_amount", comp_amt=720_000)
    # eval 26%（abs +6pct、rel +30% 双过阈），同费项占比升到 18%
    _unsettled_order(session, "u1", gmv=6_000_000, fee=1_560_000,
                     comp_key="platform_commission_amount", comp_amt=1_080_000)
    _unsettled_order(session, "u2", gmv=6_000_000, fee=1_560_000,
                     comp_key="platform_commission_amount", comp_amt=1_080_000)
    session.commit()

    res = get_fee_rate_monitor(session=session, trend_days=3, **_scope())
    assert res["status"] == "alert"
    assert round(res["current_rate"], 4) == 0.26
    assert round(res["abs_delta"], 4) == 0.06
    assert res["attributions"], "应有分项归因"
    attr = res["attributions"][0]
    assert attr["name"] == "平台佣金"
    assert round(attr["from"], 4) == 0.12 and round(attr["to"], 4) == 0.18


def test_monitor_baseline_pending_when_no_settled(session):
    """有当前 unsettled（过护栏）但无已结算基准 → baseline_pending：展示当前费率/构成，不判异常。"""
    _unsettled_order(session, "u1", gmv=6_000_000, fee=1_230_000,
                     comp_key="dynamic_commission_amount", comp_amt=720_000)
    _unsettled_order(session, "u2", gmv=6_000_000, fee=1_230_000,
                     comp_key="dynamic_commission_amount", comp_amt=720_000)
    session.commit()

    res = get_fee_rate_monitor(session=session, trend_days=3, **_scope())
    assert res["status"] == "baseline_pending"
    assert res["currency"] == "IDR"
    assert round(res["current_rate"], 4) == 0.205  # 当前预估费率有值
    assert res["components"], "当前构成应展示"
    assert res["attributions"] == []  # 无基准不归因


def test_monitor_insufficient_when_no_unsettled(session):
    """无 unsettled 预估数据（仅 baseline）→ status=insufficient，不误报。"""
    _settled_order(session, "s1", gmv=6_000_000, fee=1_200_000)
    _settled_order(session, "s2", gmv=6_000_000, fee=1_200_000)
    session.commit()

    res = get_fee_rate_monitor(session=session, trend_days=3, **_scope())
    assert res["status"] == "insufficient"
    assert res["currency"] is None
    assert res["skip_reason"]  # 有跳过原因


def test_monitor_insufficient_below_gmv_guard(session):
    """eval GMV 低于护栏 → insufficient（低基数不误报）。"""
    _settled_order(session, "s1", gmv=6_000_000, fee=1_200_000)
    _settled_order(session, "s2", gmv=6_000_000, fee=1_200_000)
    _unsettled_order(session, "u1", gmv=100_000, fee=30_000)  # 仅 0.1M < 10M 护栏
    session.commit()

    res = get_fee_rate_monitor(session=session, trend_days=3, **_scope())
    assert res["status"] == "insufficient"


def test_monitor_excludes_current_day(session):
    """当天(T)预估近乎为空、单笔高费率会顶高窗口 → 评估窗口与趋势都排除当天，只算到昨天(T-1)。"""
    # 昨天(T-1=6/23)正常费率 20%（12M GMV）
    _unsettled_order(session, "u1", gmv=6_000_000, fee=1_200_000, md=EVAL_MD)
    _unsettled_order(session, "u2", gmv=6_000_000, fee=1_200_000, md=EVAL_MD)
    # 当天(T=6/24)一笔 50% 极高费率噪声——应被排除，否则 current_rate 会被顶高
    _unsettled_order(session, "u3", gmv=1_000_000, fee=500_000, md=TODAY)
    # baseline 20%
    _settled_order(session, "s1", gmv=6_000_000, fee=1_200_000)
    _settled_order(session, "s2", gmv=6_000_000, fee=1_200_000)
    session.commit()

    res = get_fee_rate_monitor(session=session, trend_days=3, **_scope())
    # 只反映 T-1 的 20%，不含当天 50% 噪声（含则会是 ~22.3%）
    assert round(res["current_rate"], 4) == 0.20
    # 趋势最后一点是昨天(T-1)，不是当天(T)
    assert res["trend"][-1]["date"] == EVAL_MD.isoformat()
    assert TODAY.isoformat() not in [p["date"] for p in res["trend"]]
