"""广告消耗同步 flow（TTS Finance 结算口径）。

增量策略：按结算单 `statement_time` 窗口拉取。游标记录上次窗口结束时间，下次从该时间回退
overlap（结算有滞后，回看放长）。窗口内的每个结算单再翻页取其交易明细
（202501 statement_transactions），把每笔交易的三项广告费按 order_create_time 归印尼
业务日累加，按 (业务日, currency) 分组写 fact_ad_spend_daily（scope_key 幂等 upsert）。

三项广告费（均为 string）：
  gmv_max_ad_fee_amount       —— GMV Max 广告费
  tap_shop_ads_commission     —— TAP 达人广告佣金
  affiliate_ads_commission_amount —— 联盟广告佣金
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from prefect import flow, task

logger = logging.getLogger(__name__)

from core.db import SessionLocal
from core.timezone import to_business_day
from flows.network import log_egress_ip
from platforms.tiktok_shop.client import PLATFORM as TIKTOK_PLATFORM
from platforms.tiktok_shop.client import TikTokShopClient
from services.ad_spend_store import upsert_ad_spend_daily
from services.sync_state import get_cursor, record_raw_response, upsert_cursor

RESOURCE = "ad_spend"
# 结算有滞后（结算单 statement_time 晚于成交），窗口回看放长 + overlap 缓冲
OVERLAP = timedelta(days=1)
DEFAULT_LOOKBACK = timedelta(days=30)

PAGE_SIZE = 50
# 结算单与交易明细的排序字段（fetch 与 raw 审计共用，保证一致）
STATEMENT_SORT_FIELD = "statement_time"
STATEMENT_SORT_ORDER = "DESC"
TRANSACTION_SORT_FIELD = "order_create_time"

# 三项广告费字段（fee_tax_breakdown.fee 下，均为 string）
AD_FEE_FIELDS = (
    "gmv_max_ad_fee_amount",
    "tap_shop_ads_commission",
    "affiliate_ads_commission_amount",
)


def _resolve_window(
    session,
    *,
    country: str,
    shop_id: Optional[str],
    seller_id: Optional[str],
    account_id: Optional[str],
) -> tuple[int, int]:
    """Return (statement_time_ge, statement_time_lt) Unix seconds for this run."""
    now = datetime.now(timezone.utc)
    cursor = get_cursor(
        session,
        platform=TIKTOK_PLATFORM,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        resource=RESOURCE,
    )
    if cursor and cursor.window_end:
        start = cursor.window_end.replace(tzinfo=timezone.utc) - OVERLAP
    else:
        start = now - DEFAULT_LOOKBACK
    return int(start.timestamp()), int(now.timestamp())


@task(name="fetch-tiktok-ad-spend", retries=3, retry_delay_seconds=60)
def fetch_ad_spend(
    *,
    statement_time_ge: int,
    statement_time_lt: int,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """列窗口内结算单 → 对每个 statement 翻页取交易明细，收集原始交易页。

    每项原始页形如 {"statement_id": <id>, "currency": <ISO 4217>, "transactions": [...]}；
    currency 取 statement 级 `data.currency`（文档：transactions[] 下无 currency 字段，
    币种只在 statement 级），透传给 aggregate 做分组键。raw 审计与 aggregate 共用。
    """
    client = TikTokShopClient(
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    transaction_pages: list[dict[str, Any]] = []
    for stmt_page in client.iter_statements(
        statement_time_ge=statement_time_ge,
        statement_time_lt=statement_time_lt,
        page_size=PAGE_SIZE,
        sort_field=STATEMENT_SORT_FIELD,
        sort_order=STATEMENT_SORT_ORDER,
    ):
        for statement in stmt_page.get("statements", []) or []:
            statement_id = statement.get("id")
            if not statement_id:
                continue
            for txn_page in client.iter_statement_transactions(
                statement_id,
                page_size=PAGE_SIZE,
                sort_field=TRANSACTION_SORT_FIELD,
            ):
                # currency 只在 statement 级 `data.currency`（文档定义），透传给 aggregate。
                transaction_pages.append(
                    {
                        "statement_id": statement_id,
                        "currency": txn_page.get("currency"),
                        "transactions": txn_page.get("transactions", []) or [],
                    }
                )
    return transaction_pages


def _parse_fee(value) -> Decimal:
    """string/None → Decimal（容错空值）。"""
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


@task(name="aggregate-ad-spend")
def aggregate_ad_spend(transaction_pages: list[dict[str, Any]]) -> list[dict]:
    """把每笔交易的三项广告费按 (业务日, currency) 分组累加。

    currency 取每页透传的 statement 级 `data.currency`（202501 交易级无 currency 字段）。
    每笔交易：order_create_time(int64 Unix 秒)→UTC+7 印尼业务日；三项 fee 在
    fee_tax_breakdown.fee 下（string）。total_ad_spend = 三项之和。
    """
    # key = (metric_date, currency) → 累加器
    buckets: dict[tuple, dict] = {}
    for page in transaction_pages:
        # currency 取 statement 级 `data.currency`（fetch 已透传）；202501 交易级无 currency
        # 字段，从单笔取恒为 None。同一 statement 内所有交易共用此币种。
        currency = page.get("currency")
        for txn in page.get("transactions", []) or []:
            create_ts = txn.get("order_create_time")
            if create_ts is None:
                continue
            metric_date = to_business_day(
                datetime.fromtimestamp(int(create_ts), tz=timezone.utc).replace(tzinfo=None)
            )
            fee = (txn.get("fee_tax_breakdown") or {}).get("fee") or {}
            gmv_max = _parse_fee(fee.get("gmv_max_ad_fee_amount"))
            tap = _parse_fee(fee.get("tap_shop_ads_commission"))
            affiliate = _parse_fee(fee.get("affiliate_ads_commission_amount"))

            # 符号：直接用解析原值累加，不取绝对值。
            # 依据 202501 结算公式 settlement = revenue − shipping_cost − fee_tax_amount
            # − adjustment_amount，且 fee_tax_amount = fee_tax_breakdown 各项之和，故 fee 子项
            # 为正 = 对卖家扣款（即广告花费）。负值场景（如退款冲回）已用下方 warning 监控，
            # 待真实数据确认。
            order_id = txn.get("order_id")
            for field, val in (
                ("gmv_max_ad_fee_amount", gmv_max),
                ("tap_shop_ads_commission", tap),
                ("affiliate_ads_commission_amount", affiliate),
            ):
                if val < 0:
                    logger.warning(
                        "ad_spend 负值: order_id=%s field=%s value=%s",
                        order_id, field, val,
                    )

            key = (metric_date, currency)
            agg = buckets.setdefault(
                key,
                {
                    "metric_date": metric_date,
                    "currency": currency,
                    "gmv_max_fee": Decimal("0"),
                    "tap_commission": Decimal("0"),
                    "affiliate_commission": Decimal("0"),
                    "total_ad_spend": Decimal("0"),
                    "transaction_count": 0,
                },
            )
            agg["gmv_max_fee"] += gmv_max
            agg["tap_commission"] += tap
            agg["affiliate_commission"] += affiliate
            agg["total_ad_spend"] += gmv_max + tap + affiliate
            agg["transaction_count"] += 1
    return list(buckets.values())


@task(name="save-ad-spend-to-db", retries=2, retry_delay_seconds=30)
def save_ad_spend_to_db(
    transaction_pages: list[dict[str, Any]],
    rows: list[dict],
    *,
    statement_time_ge: int,
    statement_time_lt: int,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> int:
    """单事务写入 MySQL（幂等 upsert）并记录 raw payload 与 cursor。"""
    session = SessionLocal()
    try:
        raw_record = record_raw_response(
            session,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            resource=RESOURCE,
            method="GET",
            path="/finance/202501/statements/{id}/statement_transactions",
            request_params={
                "page_size": PAGE_SIZE,
                "statement_sort_field": STATEMENT_SORT_FIELD,
                "statement_sort_order": STATEMENT_SORT_ORDER,
                "transaction_sort_field": TRANSACTION_SORT_FIELD,
            },
            request_body={
                "statement_time_ge": statement_time_ge,
                "statement_time_lt": statement_time_lt,
            },
            response_payload={"pages": transaction_pages},
            http_status=200,
            business_code="0",
        )
        row_count = upsert_ad_spend_daily(
            session,
            rows,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            raw_response_id=raw_record.id,
        )
        upsert_cursor(
            session,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            resource=RESOURCE,
            window_end=datetime.fromtimestamp(statement_time_lt, tz=timezone.utc),
            extra={"row_count": row_count, "page_count": len(transaction_pages)},
        )
        session.commit()
        return row_count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@flow(name="tiktok-ad-spend-sync", log_prints=True)
def sync_ad_spend_flow(
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
):
    """广告消耗增量同步主流程。"""
    log_egress_ip()
    session = SessionLocal()
    try:
        statement_time_ge, statement_time_lt = _resolve_window(
            session,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
        )
    finally:
        session.close()

    transaction_pages = fetch_ad_spend(
        statement_time_ge=statement_time_ge,
        statement_time_lt=statement_time_lt,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    rows = aggregate_ad_spend(transaction_pages)
    row_count = save_ad_spend_to_db(
        transaction_pages,
        rows,
        statement_time_ge=statement_time_ge,
        statement_time_lt=statement_time_lt,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    print(f"广告消耗同步完成: {row_count} 个业务日分组")
    return row_count


if __name__ == "__main__":
    from flows._shop_discovery import discover_single_shop

    scope = discover_single_shop()
    print(f"Auto-discovered shop scope: {scope}")
    sync_ad_spend_flow(**scope)
