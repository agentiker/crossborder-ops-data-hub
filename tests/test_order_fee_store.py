"""交易级结算费用拆项 parse + upsert 单测。

parse_order_fees：提升列 string→Decimal、缺字段补 0、JSON 兜底存全部非零 fee/tax 子项、
无交易 id 跳过、负值保留、order_create_time 按印尼业务日（UTC+7）归集、currency 取 statement 级。
upsert_finance_transactions：同 transaction_id 写两次仅一行、逐字段刷新（含 raw_response_id）。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from models.base_models import FactFinanceTransaction
from services.order_fee_store import (
    build_finance_txn_scope_key,
    parse_order_fees,
    upsert_finance_transactions,
)


def _ts(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _txn(txn_id, create_dt, *, fee=None, tax=None, order_id="o1", adjustment_id=None, **top):
    """单笔交易：fee/tax 在 fee_tax_breakdown 下；top 为交易级汇总字段。"""
    breakdown = {}
    if fee is not None:
        breakdown["fee"] = fee
    if tax is not None:
        breakdown["tax"] = tax
    out = {
        "id": txn_id,
        "order_id": order_id,
        "order_create_time": _ts(create_dt),
        "fee_tax_breakdown": breakdown,
    }
    if adjustment_id is not None:
        out["adjustment_id"] = adjustment_id
    out.update(top)
    return out


def _page(transactions, *, currency="IDR", statement_id="s1"):
    return {"statement_id": statement_id, "currency": currency, "transactions": transactions}


# ── parse ────────────────────────────────────────────────────────────────────

def test_parse_promotes_columns_and_json_fallback():
    """提升列取 Decimal；非提升 fee/tax 子项进 JSON 兜底（保留原 string）。"""
    # 真打口径：API fee 子项 + fee_tax_amount 为负数(=对卖家扣款)，落库翻正(成本量级)。
    fee = {
        "platform_commission_amount": "-100.00",
        "referral_fee_amount": "-30.00",
        "transaction_fee_amount": "-12.50",
        "gmv_max_ad_fee_amount": "-8.00",
        "tap_shop_ads_commission": "-3.00",
        "affiliate_ads_commission_amount": "-1.00",
        # 非提升 → 仅进 JSON（原样负数）
        "seller_growth_fee_amount": "-2.20",
        "credit_card_handling_fee_amount": "-0.50",
        "insurance_fee": "0",  # 零值剔除
    }
    tax = {"vat_amount": "-5.00", "gst_amount": "0"}
    pages = [_page([
        _txn("t1", datetime(2026, 6, 8, 10, 0), fee=fee, tax=tax,
             settlement_amount="500.00", revenue_amount="650.00",
             fee_tax_amount="-155.00", shipping_cost_amount="20.00",
             adjustment_amount="0"),
    ])]
    rows = parse_order_fees(pages)
    assert len(rows) == 1
    r = rows[0]
    assert r["transaction_id"] == "t1"
    assert r["order_id"] == "o1"
    assert r["metric_date"] == date(2026, 6, 8)
    assert r["currency"] == "IDR"
    # 提升列：fee 子项与 fee_tax_amount 翻正
    assert r["platform_commission_amount"] == Decimal("100.00")
    assert r["referral_fee_amount"] == Decimal("30.00")
    assert r["transaction_fee_amount"] == Decimal("12.50")
    assert r["gmv_max_fee"] == Decimal("8.00")
    assert r["tap_commission"] == Decimal("3.00")
    assert r["affiliate_commission"] == Decimal("1.00")
    assert r["fee_tax_amount"] == Decimal("155.00")
    # revenue/settlement/shipping/adjustment 保持原始符号
    assert r["settlement_amount"] == Decimal("500.00")
    assert r["revenue_amount"] == Decimal("650.00")
    assert r["shipping_cost_amount"] == Decimal("20.00")
    assert r["adjustment_amount"] == Decimal("0")
    # JSON 兜底：含全部非零 fee 子项（原样保真负号），剔除零值
    assert r["fee_breakdown"]["seller_growth_fee_amount"] == "-2.20"
    assert r["fee_breakdown"]["credit_card_handling_fee_amount"] == "-0.50"
    assert "insurance_fee" not in r["fee_breakdown"]
    assert r["tax_breakdown"] == {"vat_amount": "-5.00"}


def test_parse_missing_fields_default_zero():
    """缺 fee/汇总字段 → 提升列补 0，JSON 为空 dict。"""
    pages = [_page([_txn("t1", datetime(2026, 6, 8, 10, 0))])]
    r = parse_order_fees(pages)[0]
    assert r["platform_commission_amount"] == Decimal("0")
    assert r["settlement_amount"] == Decimal("0")
    assert r["fee_breakdown"] == {}
    assert r["tax_breakdown"] == {}


def test_parse_skips_txn_without_id():
    """无交易 id → 跳过（无法幂等去重）。"""
    pages = [_page([
        {"order_id": "o1", "order_create_time": _ts(datetime(2026, 6, 8))},  # 无 id
        _txn("t2", datetime(2026, 6, 8, 10, 0), fee={"referral_fee_amount": "1.00"}),
    ])]
    rows = parse_order_fees(pages)
    assert [r["transaction_id"] for r in rows] == ["t2"]


def test_parse_flips_fee_sign():
    """fee 翻符号：API 负数(扣款)→正成本；正值(退款冲回 credit)→负成本。adjustment 不翻、JSON 原样。"""
    pages = [_page([
        _txn("t1", datetime(2026, 6, 8, 10, 0),
             fee={"platform_commission_amount": "30.00"},  # 正=credit/退款冲回
             fee_tax_amount="-100.00", adjustment_amount="-5.00"),
    ])]
    r = parse_order_fees(pages)[0]
    assert r["platform_commission_amount"] == Decimal("-30.00")  # credit → 负成本
    assert r["fee_tax_amount"] == Decimal("100.00")  # 负 → 正
    assert r["adjustment_amount"] == Decimal("-5.00")  # adjustment 保持原符号
    assert r["fee_breakdown"]["platform_commission_amount"] == "30.00"  # JSON 原样


def test_parse_business_day_utc7():
    """order_create_time UTC 21:00 → +7 跨日到次日业务日。"""
    pages = [_page([_txn("t1", datetime(2026, 6, 8, 21, 0))])]
    r = parse_order_fees(pages)[0]
    assert r["metric_date"] == date(2026, 6, 9)


def test_parse_currency_from_statement_level():
    """currency 取 page(statement) 级，非交易级。"""
    pages = [_page([_txn("t1", datetime(2026, 6, 8))], currency="USD")]
    assert parse_order_fees(pages)[0]["currency"] == "USD"


# ── upsert 幂等 ──────────────────────────────────────────────────────────────

def _row(txn_id="t1", *, order_id="o1", md=date(2026, 6, 8), commission="100", settlement="500"):
    return {
        "transaction_id": txn_id,
        "order_id": order_id,
        "adjustment_id": None,
        "metric_date": md,
        "currency": "IDR",
        "fee_breakdown": {"referral_fee_amount": "30.00"},
        "tax_breakdown": {},
        "settlement_amount": Decimal(settlement),
        "revenue_amount": Decimal("650"),
        "fee_tax_amount": Decimal("155"),
        "shipping_cost_amount": Decimal("20"),
        "adjustment_amount": Decimal("0"),
        "platform_commission_amount": Decimal(commission),
        "referral_fee_amount": Decimal("30"),
        "transaction_fee_amount": Decimal("12.5"),
        "gmv_max_fee": Decimal("8"),
        "tap_commission": Decimal("3"),
        "affiliate_commission": Decimal("1"),
    }


def test_upsert_idempotent_single_row(session):
    """同 transaction_id 写两次仅一行。"""
    n1 = upsert_finance_transactions(session, [_row()], shop_id="shop-1", raw_response_id=11)
    session.commit()
    n2 = upsert_finance_transactions(session, [_row()], shop_id="shop-1", raw_response_id=11)
    session.commit()
    assert (n1, n2) == (1, 1)
    assert session.query(FactFinanceTransaction).count() == 1


def test_upsert_updates_fields_and_raw_id(session):
    """重跑逐字段刷新（含 JSON 与 raw_response_id）。"""
    upsert_finance_transactions(
        session, [_row(commission="100", settlement="500")],
        shop_id="shop-1", raw_response_id=100,
    )
    session.commit()
    upsert_finance_transactions(
        session, [_row(commission="80", settlement="450")],
        shop_id="shop-1", raw_response_id=200,
    )
    session.commit()
    assert session.query(FactFinanceTransaction).count() == 1
    rec = session.query(FactFinanceTransaction).one()
    assert rec.platform_commission_amount == Decimal("80")
    assert rec.settlement_amount == Decimal("450")
    assert rec.raw_response_id == 200
    assert rec.fee_breakdown == {"referral_fee_amount": "30.00"}


def test_upsert_distinct_transactions_distinct_rows(session):
    """不同 transaction_id → 两行；scope_key 不同。"""
    upsert_finance_transactions(
        session, [_row("t1"), _row("t2")], shop_id="shop-1", raw_response_id=1,
    )
    session.commit()
    assert session.query(FactFinanceTransaction).count() == 2
    k1 = build_finance_txn_scope_key(transaction_id="t1", platform="tiktok_shop", shop_id="shop-1")
    k2 = build_finance_txn_scope_key(transaction_id="t2", platform="tiktok_shop", shop_id="shop-1")
    assert k1 != k2
