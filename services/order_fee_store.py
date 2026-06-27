"""交易级结算费用拆项的解析 + 幂等落库（按 transaction_id upsert）。

数据源与 ad_spend 同一份 fetch（flows.sync_ad_spend 的 transaction_pages），但本模块把每笔
交易的**全部** fee/tax 拆项 + 交易级汇总解析成一行，存 FactFinanceTransaction。日级广告费仍
由 services.ad_spend_store 走旧表，两者解耦并存。
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from core.timezone import to_business_day
from models.base_models import FactFinanceTransaction
from services.scoping import build_scope_key

# 提升为独立列的头部扣点：fee 子键 → 模型列名
PROMOTED_FEE_COLUMNS = {
    "platform_commission_amount": "platform_commission_amount",
    "referral_fee_amount": "referral_fee_amount",
    "transaction_fee_amount": "transaction_fee_amount",
    "gmv_max_ad_fee_amount": "gmv_max_fee",
    "tap_shop_ads_commission": "tap_commission",
    "affiliate_ads_commission_amount": "affiliate_commission",
}

# 交易级汇总字段：交易顶层键 → 模型列名
PROMOTED_TXN_COLUMNS = {
    "settlement_amount": "settlement_amount",
    "revenue_amount": "revenue_amount",
    "fee_tax_amount": "fee_tax_amount",
    "shipping_cost_amount": "shipping_cost_amount",
    "adjustment_amount": "adjustment_amount",
}


def _to_decimal(value) -> Decimal:
    """string/None → Decimal（容错空值）。"""
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def _nonzero_map(raw: dict) -> dict:
    """保留原始 string 值的非零子项（'0'/''/None 剔除），平台新增费种自动入库。"""
    out: dict[str, str] = {}
    for key, val in (raw or {}).items():
        if val in (None, "", "0", "0.0", "0.00"):
            continue
        try:
            if _to_decimal(val) == 0:
                continue
        except Exception:
            # 非数值（理论上不会出现在 fee/tax）原样保留，不丢数据
            out[key] = val
            continue
        out[key] = val
    return out


def parse_order_fees(transaction_pages: list[dict[str, Any]]) -> list[dict]:
    """把每笔交易解析成一行费用拆项（不聚合，保交易粒度）。

    currency 取每页透传的 statement 级 `data.currency`（202501 交易级无此字段）。无交易 `id`
    者跳过（无法幂等去重）。提升列取 Decimal、JSON 兜底存全部非零 fee/tax 子项。
    """
    rows: list[dict] = []
    for page in transaction_pages:
        currency = page.get("currency")
        for txn in page.get("transactions", []) or []:
            transaction_id = txn.get("id")
            if not transaction_id:
                continue
            create_ts = txn.get("order_create_time")
            if create_ts is None:
                metric_date = None
            else:
                metric_date = to_business_day(
                    datetime.fromtimestamp(int(create_ts), tz=timezone.utc).replace(tzinfo=None)
                )

            breakdown = txn.get("fee_tax_breakdown") or {}
            fee = breakdown.get("fee") or {}
            tax = breakdown.get("tax") or {}

            row: dict[str, Any] = {
                "transaction_id": str(transaction_id),
                "order_id": txn.get("order_id"),
                "adjustment_id": txn.get("adjustment_id"),
                "metric_date": metric_date,
                "currency": currency,
                "fee_breakdown": _nonzero_map(fee),
                "tax_breakdown": _nonzero_map(tax),
            }
            for src, col in PROMOTED_TXN_COLUMNS.items():
                val = _to_decimal(txn.get(src))
                # fee_tax_amount API 为负数(=对卖家扣款) → 翻正(成本量级)，与 profit/fee_rate 口径一致；
                # revenue/settlement/shipping/adjustment 保持原始符号。
                row[col] = -val if src == "fee_tax_amount" else val
            for src, col in PROMOTED_FEE_COLUMNS.items():
                row[col] = -_to_decimal(fee.get(src))  # fee 子项同为负 → 翻正
            rows.append(row)
    return rows


def build_finance_txn_scope_key(
    *,
    transaction_id: str,
    platform: str,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> str:
    """交易级唯一键：维度 + `finance_txn:<transaction_id>`，transaction_id 全局唯一保证幂等。"""
    return build_scope_key(
        platform=platform,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        resource=f"finance_txn:{transaction_id}",
    )


# 重跑时需逐字段刷新的列（提升列 + JSON + 维度无关的可变字段）
_REFRESH_COLUMNS = (
    "order_id",
    "adjustment_id",
    "metric_date",
    "currency",
    "fee_breakdown",
    "tax_breakdown",
    *PROMOTED_TXN_COLUMNS.values(),
    *PROMOTED_FEE_COLUMNS.values(),
)


def upsert_finance_transactions(
    session,
    rows: list[dict],
    *,
    platform: str = "tiktok_shop",
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    raw_response_id: Optional[int] = None,
) -> int:
    """交易级费用行按 scope_key（transaction_id）幂等 upsert。

    `rows` 来自 parse_order_fees。同 transaction_id 写两次仅一行、逐字段刷新（含 raw_response_id）；
    末尾 flush，由调用方 commit。
    """
    for row in rows:
        scope_key = build_finance_txn_scope_key(
            transaction_id=row["transaction_id"],
            platform=platform,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
        )
        existing = (
            session.query(FactFinanceTransaction)
            .filter_by(scope_key=scope_key)
            .first()
        )
        if existing:
            for col in _REFRESH_COLUMNS:
                setattr(existing, col, row.get(col))
            existing.raw_response_id = raw_response_id
        else:
            session.add(
                FactFinanceTransaction(
                    platform=platform,
                    country=country,
                    shop_id=shop_id,
                    seller_id=seller_id,
                    account_id=account_id,
                    scope_key=scope_key,
                    transaction_id=row["transaction_id"],
                    raw_response_id=raw_response_id,
                    **{col: row.get(col) for col in _REFRESH_COLUMNS},
                )
            )
    session.flush()
    return len(rows)
