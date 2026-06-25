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

    ts = int(datetime(2026, 6, 24, 5, 0, tzinfo=timezone.utc).timestamp())
    pages = [{"transactions": [
        {"id": "T1", "order_id": "O1", "currency": "IDR", "order_create_time": ts,
         "estimated_fee_amount": "1000", "estimated_revenue_amount": "9000",
         "fee_tax_breakdown": {"fee": {"platform_commission_amount": "800",
                                       "gmv_max_ad_fee_amount": "200", "zero_item": "0"}}},
        {"no_id": "skip"},
    ]}]
    rows = parse_unsettled_fees(pages)
    assert len(rows) == 1
    r = rows[0]
    assert r["transaction_id"] == "T1" and r["order_id"] == "O1"
    assert r["estimated_fee_amount"] == Decimal("1000")
    assert r["gmv_max_fee"] == Decimal("200")
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


# ── 6. 利润聚合：扣点双源去重 + 广告不双算 + 退货率 + 折 RMB ──────────────────
def test_compute_daily_profit(session, monkeypatch):
    import services.profit_aggregation as PA

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
