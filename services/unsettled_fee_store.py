"""未结算订单预估费用的解析 + 全量替换落库（GET /finance/202507/orders/unsettled）。

与 order_fee_store（结算口径）对称，但本模块存的是 TikTok 官方**预估额**到 fact_unsettled_fee。
关键差异：订单结算后从 unsettled 接口消失 → 采集用**全量替换**（每店每业务日先 DELETE 当日旧行
再插当次全量），预估行随结算自然消退，无需过期任务。幂等键仍用 transaction_id（重跑当日不重复）。

⚠️ 字段命名以生产店真打为准：沙箱 total_count=0 验不了。命名若与下方假设不符，仅改本文件映射。
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from core.timezone import to_business_day
from models.base_models import FactUnsettledFee
from services.scoping import build_scope_key

# 三项广告费（fee_tax_breakdown.fee 下，命名同结算单）：fee 子键 → 模型列名。
# 利润里广告费单列，故这三项从 estimated_fee_amount 里减掉避免与广告费双算（见 profit_aggregation）。
# 真打口径：API 子项为负数(=对卖家扣款)，落库统一翻成正数(成本量级)。
# 注：生产真打本店 fee 下无 gmv_max_ad_fee_amount / tap_shop_ads_commission 键（无该类广告）→ 取 0；
#     仅 affiliate_ads_commission_amount 有值。键保留，后续若出现该类广告自动入库。
PROMOTED_AD_FEE_COLUMNS = {
    "gmv_max_ad_fee_amount": "gmv_max_fee",
    "tap_shop_ads_commission": "tap_commission",
    "affiliate_ads_commission_amount": "affiliate_commission",
}

# 交易顶层"收入/结算"键（API 即正数，原样落库）→ 模型列名。
# ⚠️ 键名以 GET /finance/202507/orders/unsettled 生产真打为准：est_* 前缀，非 estimated_*_amount。
PROMOTED_REVENUE_COLUMNS = {
    "est_revenue_amount": "estimated_revenue_amount",
    "est_settlement_amount": "estimated_settlement_amount",
}

# 交易顶层"扣费"键：API est_fee_tax_amount 为负数(=对卖家扣款)，落库翻成正数(成本量级)，
# 与 order_fee_store / profit_aggregation / fee_rate_metrics 的"为正=扣款"口径一致。
# 接口无独立 adjustment 源键（est_revenue + est_fee_tax = est_settlement，无调整项）→ 落 0。
PROMOTED_FEE_COLUMNS = {
    "est_fee_tax_amount": "estimated_fee_amount",
}


def _to_decimal(value) -> Decimal:
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
            out[key] = val
            continue
        out[key] = val
    return out


def parse_unsettled_fees(pages: list[dict[str, Any]]) -> list[dict]:
    """把每笔未结算交易解析成一行预估费用（保交易粒度）。

    `pages` 来自 flow 收集的 `[{"transactions": [...]}]`（unsettled 交易级自带 currency）。
    无交易 `id` 者跳过（无法幂等去重）。提升列取 Decimal、fee 非零子项入 JSON 兜底。
    """
    rows: list[dict] = []
    for page in pages:
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

            fee = (txn.get("fee_tax_breakdown") or {}).get("fee") or {}

            row: dict[str, Any] = {
                "transaction_id": str(transaction_id),
                "order_id": txn.get("order_id"),
                "metric_date": metric_date,
                "currency": txn.get("currency"),
                "fee_breakdown": _nonzero_map(fee),
            }
            for src, col in PROMOTED_REVENUE_COLUMNS.items():
                row[col] = _to_decimal(txn.get(src))
            for src, col in PROMOTED_FEE_COLUMNS.items():
                row[col] = -_to_decimal(txn.get(src))  # API 负数(扣款) → 正数(成本)
            row["estimated_adjustment_amount"] = Decimal("0")  # 接口无调整项源键
            for src, col in PROMOTED_AD_FEE_COLUMNS.items():
                row[col] = -_to_decimal(fee.get(src))  # 广告费同为负 → 翻正
            rows.append(row)
    return rows


def build_unsettled_scope_key(
    *,
    transaction_id: str,
    platform: str,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> str:
    """交易级唯一键：维度 + `unsettled:<transaction_id>`。"""
    return build_scope_key(
        platform=platform,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        resource=f"unsettled:{transaction_id}",
    )


# 重写行需刷新的列（提升列 + JSON + 可变维度无关字段）
_REFRESH_COLUMNS = (
    "order_id",
    "metric_date",
    "currency",
    "fee_breakdown",
    "estimated_fee_amount",
    "estimated_revenue_amount",
    "estimated_settlement_amount",
    "estimated_adjustment_amount",
    *PROMOTED_AD_FEE_COLUMNS.values(),
)


def replace_unsettled_for_day(
    session,
    rows: list[dict],
    *,
    metric_date,
    platform: str = "tiktok_shop",
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    raw_response_id: Optional[int] = None,
) -> int:
    """**全量替换**某店某业务日的未结算预估行：先 DELETE 当日旧行，再插 rows。

    `rows` 须全部属于同一 `metric_date`（调用方按业务日分组传入）。这样订单结算后从接口消失
    → 当日 DELETE 后不再插入，预估行自动消退。末尾 flush，由调用方 commit。
    """
    # 1) 删当日旧预估行（按维度 + metric_date）
    del_q = session.query(FactUnsettledFee).filter(
        FactUnsettledFee.platform == platform,
        FactUnsettledFee.country == country,
        FactUnsettledFee.metric_date == metric_date,
    )
    if shop_id is not None:
        del_q = del_q.filter(FactUnsettledFee.shop_id == shop_id)
    if seller_id is not None:
        del_q = del_q.filter(FactUnsettledFee.seller_id == seller_id)
    if account_id is not None:
        del_q = del_q.filter(FactUnsettledFee.account_id == account_id)
    del_q.delete(synchronize_session=False)

    # 2) 插当次全量（transaction_id 幂等键，理论上 DELETE 后无冲突）
    for row in rows:
        scope_key = build_unsettled_scope_key(
            transaction_id=row["transaction_id"],
            platform=platform,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
        )
        session.add(
            FactUnsettledFee(
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
