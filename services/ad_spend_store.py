"""每日广告消耗的幂等落库（按 scope_key upsert）。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from models.base_models import FactAdSpendDaily
from services.scoping import build_scope_key


def build_ad_spend_scope_key(
    *,
    platform: str,
    metric_date: date,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> str:
    """广告消耗行的唯一键：维度（平台+国家+店+卖家+账号）+ 业务日，保证幂等。

    仿 analytics.profit_alerts.build_profit_scope_key，resource 形如
    `ad_spend:<metric_date>`，复用 services.scoping.build_scope_key 的统一拼装规则。
    """
    return build_scope_key(
        platform=platform,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        resource=f"ad_spend:{metric_date.isoformat()}",
    )


def upsert_ad_spend_daily(
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
    """按 (业务日, currency) 聚合后的广告费行幂等 upsert。

    `rows` 每项是一条聚合记录，字段：
      metric_date(date) / currency(str|None) / gmv_max_fee / tap_commission /
      affiliate_commission / total_ad_spend(均 Decimal) / transaction_count(int)。
    按 scope_key filter_by().first()：有则逐字段更新、无则 add；末尾 flush，由调用方 commit。
    """
    for row in rows:
        scope_key = build_ad_spend_scope_key(
            platform=platform,
            metric_date=row["metric_date"],
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
        )
        existing = (
            session.query(FactAdSpendDaily)
            .filter_by(scope_key=scope_key)
            .first()
        )
        if existing:
            existing.currency = row.get("currency")
            existing.gmv_max_fee = row["gmv_max_fee"]
            existing.tap_commission = row["tap_commission"]
            existing.affiliate_commission = row["affiliate_commission"]
            existing.total_ad_spend = row["total_ad_spend"]
            existing.transaction_count = row.get("transaction_count", 0)
            existing.raw_response_id = raw_response_id
        else:
            session.add(
                FactAdSpendDaily(
                    metric_date=row["metric_date"],
                    platform=platform,
                    country=country,
                    shop_id=shop_id,
                    seller_id=seller_id,
                    account_id=account_id,
                    scope_key=scope_key,
                    currency=row.get("currency"),
                    gmv_max_fee=row["gmv_max_fee"],
                    tap_commission=row["tap_commission"],
                    affiliate_commission=row["affiliate_commission"],
                    total_ad_spend=row["total_ad_spend"],
                    transaction_count=row.get("transaction_count", 0),
                    raw_response_id=raw_response_id,
                )
            )
    session.flush()
    return len(rows)
