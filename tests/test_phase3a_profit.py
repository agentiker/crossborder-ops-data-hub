"""阶段3a 预估利润单测：fx / scope_key kind / unsettled 解析 / 成本导入 / 退货率 /
利润聚合（扣点双源去重·广告不双算·退货率·折RMB）/ 利润卡取数。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from analytics.profit_alerts import ProfitRecordInput, build_profit_scope_key
from models.base_models import (
    FactFinanceTransaction,
    FactUnsettledFee,
    OrderHeader,
    ProductCost,
    ReturnRateConfig,
)

D = date(2026, 6, 24)
ACC = "ecom-app"


# ── 1. fx 量级 ──────────────────────────────────────────────────────────────
def test_fx_magnitude():
    from services.fx_rate import convert_idr_to_rmb, get_idr_to_rmb

    assert get_idr_to_rmb() == Decimal("0.00045")
    assert convert_idr_to_rmb(Decimal("10000000")) == Decimal("4500.00000")
    assert convert_idr_to_rmb(None) == Decimal("0")


# ── 2. scope_key 含 profit_kind ─────────────────────────────────────────────
def test_scope_key_includes_kind():
    base = dict(metric_date=D, platform="tiktok_shop", shop_id="S1", account_id=ACC)
    est = build_profit_scope_key(ProfitRecordInput(**base, profit_kind="estimated"))
    settled = build_profit_scope_key(ProfitRecordInput(**base, profit_kind="settled"))
    assert "profit:estimated:" in est
    assert "profit:settled:" in settled
    assert est != settled


# ── 3. unsettled 解析 ───────────────────────────────────────────────────────
def test_parse_unsettled_fees():
    from services.unsettled_fee_store import parse_unsettled_fees

    # 真打口径：est_* 顶层键、扣费为负数(落库翻正)、est_revenue + est_fee_tax = est_settlement
    ts = int(datetime(2026, 6, 24, 5, 0, tzinfo=timezone.utc).timestamp())
    pages = [{"transactions": [
        {"id": "T1", "order_id": "O1", "currency": "IDR", "order_create_time": ts,
         "est_fee_tax_amount": "-1000", "est_revenue_amount": "9000",
         "est_settlement_amount": "8000",
         "fee_tax_breakdown": {"fee": {"dynamic_commission_amount": "-800",
                                       "gmv_max_ad_fee_amount": "-200", "zero_item": "0"}}},
        {"no_id": "skip"},
    ]}]
    rows = parse_unsettled_fees(pages)
    assert len(rows) == 1
    r = rows[0]
    assert r["transaction_id"] == "T1" and r["order_id"] == "O1"
    assert r["estimated_fee_amount"] == Decimal("1000")  # 负→正
    assert r["estimated_revenue_amount"] == Decimal("9000")
    assert r["estimated_settlement_amount"] == Decimal("8000")
    assert r["estimated_adjustment_amount"] == Decimal("0")  # 接口无调整项
    assert r["gmv_max_fee"] == Decimal("200")  # 广告费负→正
    assert "zero_item" not in r["fee_breakdown"]  # 零项剔除
    assert parse_unsettled_fees([{"transactions": []}]) == []


# ── 4. 成本 CSV 导入 ────────────────────────────────────────────────────────
def test_import_costs_from_rows(session):
    from services.product_cost_store import get_cost_map, import_costs_from_rows

    rows = [
        {"seller_sku": "SKU-A", "unit_cost_rmb": "12.5"},
        {"seller_sku": "SKU-B", "unit_cost_rmb": "8", "note": "含运费"},
        {"seller_sku": "", "unit_cost_rmb": "5"},          # 坏行：缺 sku
        {"seller_sku": "SKU-C", "unit_cost_rmb": "abc"},   # 坏行：非数字
        {"seller_sku": "SKU-D", "unit_cost_rmb": "-1"},    # 坏行：负数
    ]
    res = import_costs_from_rows(session, rows, account_id=ACC, platform="tiktok_shop")
    session.commit()
    assert res["inserted"] == 2 and res["updated"] == 0 and len(res["errors"]) == 3
    # 再导一次同 SKU → update
    res2 = import_costs_from_rows(
        session, [{"seller_sku": "SKU-A", "unit_cost_rmb": "13"}],
        account_id=ACC, platform="tiktok_shop",
    )
    session.commit()
    assert res2["updated"] == 1 and res2["inserted"] == 0
    cmap = get_cost_map(account_id=ACC, platform="tiktok_shop", session=session)
    assert cmap["SKU-A"] == Decimal("13") and cmap["SKU-B"] == Decimal("8")


# ── 5. 退货率三级优先级 ─────────────────────────────────────────────────────
def test_return_rate_priority(session):
    from services.return_rate import get_return_rate

    # 无配置 → settings 默认
    assert get_return_rate(account_id=ACC, platform="tiktok_shop", session=session) == Decimal("0.05")
    session.add_all([
        ReturnRateConfig(account_id=ACC, platform="tiktok_shop", scope_level="default",
                         scope_value="", return_rate=Decimal("0.08")),
        ReturnRateConfig(account_id=ACC, platform="tiktok_shop", scope_level="category",
                         scope_value="dress", return_rate=Decimal("0.12")),
        ReturnRateConfig(account_id=ACC, platform="tiktok_shop", scope_level="sku",
                         scope_value="SKU-X", return_rate=Decimal("0.20")),
    ])
    session.commit()
    assert get_return_rate(account_id=ACC, platform="tiktok_shop", session=session) == Decimal("0.08")
    assert get_return_rate(account_id=ACC, platform="tiktok_shop", category="dress",
                           session=session) == Decimal("0.12")
    assert get_return_rate(account_id=ACC, platform="tiktok_shop", category="dress",
                           sku="SKU-X", session=session) == Decimal("0.20")


def test_effective_return_rate_real_over_config(session, monkeypatch):
    """真实历史率优先：样本足→用真实率；样本不足/无 GMV→回落配置率链。"""
    import services.return_rate as RR
    from datetime import date

    # 配置一个 default 配置率 0.08（作回落基准）
    session.add(ReturnRateConfig(account_id=ACC, platform="tiktok_shop",
                                 scope_level="default", scope_value="", return_rate=Decimal("0.08")))
    session.commit()

    # 真实率样本充足（单数≥20、GMV>0）→ 用真实率 0.0046
    monkeypatch.setattr(RR, "_real_historical_rate", lambda **k: Decimal("0.0046"))
    assert RR.get_effective_return_rate(
        account_id=ACC, platform="tiktok_shop", shop_id="S1",
        as_of=date(2026, 7, 4), session=session,
    ) == Decimal("0.0046")

    # 真实率不可信（样本不足→None）→ 回落配置率 0.08
    monkeypatch.setattr(RR, "_real_historical_rate", lambda **k: None)
    assert RR.get_effective_return_rate(
        account_id=ACC, platform="tiktok_shop", shop_id="S1",
        as_of=date(2026, 7, 4), session=session,
    ) == Decimal("0.08")


def test_real_historical_rate_sample_guard(session, monkeypatch):
    """样本护栏：退款单数 < 阈值 或 GMV≤0 → 返回 None（不可信，交回落）。"""
    import services.return_rate as RR
    from datetime import date

    # 单数够但正常 → 返回真实率（get_refund_summary 是函数内 import，patch 源模块 refund_metrics）
    import services.refund_metrics as RM
    monkeypatch.setattr(RM, "get_refund_summary", lambda **k: {
        "refund_rate": 0.005, "refund_order_count": 50, "gmv": 1e9,
    })
    assert RR._real_historical_rate(
        platform="tiktok_shop", country=None, shop_id="S1", as_of=date(2026, 7, 4),
    ) == Decimal("0.005")

    # 单数不足 → None
    monkeypatch.setattr(RM, "get_refund_summary", lambda **k: {
        "refund_rate": 0.005, "refund_order_count": 3, "gmv": 1e9,
    })
    assert RR._real_historical_rate(
        platform="tiktok_shop", country=None, shop_id="S1", as_of=date(2026, 7, 4),
    ) is None

    # GMV=0 → None
    monkeypatch.setattr(RM, "get_refund_summary", lambda **k: {
        "refund_rate": None, "refund_order_count": 0, "gmv": 0,
    })
    assert RR._real_historical_rate(
        platform="tiktok_shop", country=None, shop_id="S1", as_of=date(2026, 7, 4),
    ) is None


# ── 6. 利润聚合：扣点双源去重 + 广告不双算 + 退货率 + 折 RMB ──────────────────
def test_compute_daily_profit(session, monkeypatch):
    import services.profit_aggregation as PA

    # 钉死汇率为固定值：本测试只验聚合逻辑（去重/不双算/折算比例），不受 fact_exchange_rate
    # 有无真实牌价行影响（hp 上跑测试时真库已有牌价，否则 0.00045 断言会挂）。
    monkeypatch.setattr(PA, "convert_idr_to_rmb",
                        lambda amt, on_date=None: (Decimal("0") if amt is None
                                                   else Decimal(str(amt)) * Decimal("0.00045")))

    # GMV/销量 mock（get_gmv_summary 不接受 session，查真 DB，必须 mock）
    monkeypatch.setattr(PA.order_metrics, "get_gmv_summary",
                        lambda **k: {"gmv": 10000, "order_count": 3, "units_sold": 5})
    monkeypatch.setattr(PA.order_metrics, "get_units_by_seller_sku", lambda **k: {})

    dim = dict(platform="tiktok_shop", country="GLOBAL", shop_id="S1",
               seller_id=None, account_id=ACC, metric_date=D)
    # 未结算：订单 A、B（扣点含广告，需减广告）
    session.add_all([
        FactUnsettledFee(scope_key="u:A", transaction_id="tA", order_id="A",
                         estimated_fee_amount=Decimal("1000"), gmv_max_fee=Decimal("200"), **dim),
        FactUnsettledFee(scope_key="u:B", transaction_id="tB", order_id="B",
                         estimated_fee_amount=Decimal("2000"), tap_commission=Decimal("300"), **dim),
    ])
    # 已结算：订单 B（应被去重排除）、C
    session.add_all([
        FactFinanceTransaction(scope_key="f:B", transaction_id="fB", order_id="B",
                               fee_tax_amount=Decimal("2100"), gmv_max_fee=Decimal("300"), **dim),
        FactFinanceTransaction(scope_key="f:C", transaction_id="fC", order_id="C",
                               fee_tax_amount=Decimal("1500"), affiliate_commission=Decimal("100"), **dim),
    ])
    session.commit()

    rec = PA.compute_daily_profit(
        metric_date=D, platform="tiktok_shop", country="GLOBAL",
        shop_id="S1", seller_id=None, account_id=ACC, session=session,
    )
    # 扣点(IDR) = 未结算[(1000-200)+(2000-300)=2500] + 已结算[C:(1500-100)=1400, B 排除] = 3900
    # 广告(IDR) = 未结算[200+300=500] + 已结算[C:100, B 排除] = 600
    # 退货(IDR) = 10000 × 0.05 = 500
    # 折 RMB ×0.00045
    assert rec.commission_fee == Decimal("3900") * Decimal("0.00045")
    assert rec.ad_cost == Decimal("600") * Decimal("0.00045")
    assert rec.refund_amount == Decimal("500") * Decimal("0.00045")
    assert rec.gmv == Decimal("10000") * Decimal("0.00045")
    assert rec.product_cost == Decimal("0")
    assert rec.currency == "CNY" and rec.profit_kind == "estimated"
    assert rec.order_count == 3 and rec.units_sold == 5


# ── 6b. 下单口径 GMV（COD：扣点-GMV 同队列）──────────────────────────────────
def test_gmv_summary_by_create_includes_cod_excludes_cancelled(session, monkeypatch):
    """下单口径(by_create)含未付款 COD 在途单、排除 CANCELLED；付款口径仅计已付款。"""
    import services.order_metrics as OM
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(OM, "SessionLocal",
                        sessionmaker(bind=session.get_bind(), expire_on_commit=False))

    # 业务日 D=2026-06-24 印尼(UTC+7)，UTC 06-24 05:00 落窗内
    ct = datetime(2026, 6, 24, 5, 0)
    common = dict(platform="tiktok_shop", country="ID", shop_id="shop-1", currency="IDR")
    session.add_all([
        # COD 在途：已下单未付款 → 下单口径计入、付款口径漏
        OrderHeader(order_id="cod1", idempotency_key="ik-cod1", total_amount=Decimal("100"),
                    order_status="IN_TRANSIT", is_cod=True, create_time=ct, paid_time=None, **common),
        # 预付已付款 → 两口径都计
        OrderHeader(order_id="pp1", idempotency_key="ik-pp1", total_amount=Decimal("50"),
                    order_status="DELIVERED", is_cod=False, create_time=ct, paid_time=ct, **common),
        # 取消单 → 下单口径排除
        OrderHeader(order_id="cx1", idempotency_key="ik-cx1", total_amount=Decimal("999"),
                    order_status="CANCELLED", create_time=ct, paid_time=None, **common),
    ])
    session.commit()

    kw = dict(start_date=D, end_date=D, platform="tiktok_shop", country="ID", shop_id="shop-1")
    by_create = OM.get_gmv_summary(**kw, by_create=True)
    by_paid = OM.get_gmv_summary(**kw)
    assert by_create["gmv"] == 150.0 and by_create["order_count"] == 2  # cod1+pp1，排除 cancelled
    assert by_paid["gmv"] == 50.0 and by_paid["order_count"] == 1  # 仅 pp1 已付款


# ── 7. 利润卡取数：available + estimated/settled 分套 ────────────────────────
def test_get_profit_card(session):
    from services.metrics_store import upsert_daily_profit
    from services.profit_summary import get_profit_card

    # 无数据 → available=False
    card0 = get_profit_card(start_date=D, end_date=D, platform="tiktok_shop", session=session)
    assert card0["available"] is False and card0["estimated"] is None

    rec = ProfitRecordInput(
        metric_date=D, platform="tiktok_shop", shop_id="S1", account_id=ACC,
        gmv=Decimal("100"), commission_fee=Decimal("10"), ad_cost=Decimal("5"),
        product_cost=Decimal("20"), refund_amount=Decimal("5"),
        currency="CNY", profit_kind="estimated",
    )
    upsert_daily_profit(session, rec)
    session.commit()
    card = get_profit_card(start_date=D, end_date=D, platform="tiktok_shop", session=session)
    assert card["available"] is True
    assert card["estimated"]["gmv"] == 100.0
    assert card["estimated"]["gross_profit"] == 60.0  # 100-10-5-20-5
    assert card["settled"] is None
    # 单日窗口、1 天有数据 → 覆盖完整
    assert card["expected_days"] == 1
    assert card["covered_days"] == 1
    assert card["coverage_complete"] is True


def test_get_profit_card_coverage_incomplete(session):
    """窗口 7 天但只聚合了 1 天 → coverage_complete=False（缺天可见，不静默少算）。"""
    from datetime import timedelta

    from services.metrics_store import upsert_daily_profit
    from services.profit_summary import get_profit_card

    rec = ProfitRecordInput(
        metric_date=D, platform="tiktok_shop", shop_id="S1", account_id=ACC,
        gmv=Decimal("100"), commission_fee=Decimal("10"), ad_cost=Decimal("5"),
        product_cost=Decimal("20"), refund_amount=Decimal("5"),
        currency="CNY", profit_kind="estimated",
    )
    upsert_daily_profit(session, rec)
    session.commit()

    start = D - timedelta(days=6)
    card = get_profit_card(start_date=start, end_date=D, platform="tiktok_shop", session=session)
    assert card["available"] is True       # 有数据
    assert card["expected_days"] == 7
    assert card["covered_days"] == 1
    assert card["coverage_complete"] is False
