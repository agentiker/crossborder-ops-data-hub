"""每日 GMV Max 花费的幂等落库（Marketing API 口径，按 scope_key upsert）。

仿 services.ad_spend_store，但写 fact_gmv_max_spend_daily（与 Finance 结算口径隔离）。
scope 维度：platform=tiktok_business，shop_id=store_id，seller_id=advertiser_id。
resource 形如 `gmv_max_spend:<metric_date>`，同一 (店, 广告主, 日) 幂等一行。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from services.scoping import build_scope_key

PLATFORM = "tiktok_business"


def build_gmv_max_scope_key(
    *,
    metric_date: date,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> str:
    return build_scope_key(
        platform=PLATFORM,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        resource=f"gmv_max_spend:{metric_date.isoformat()}",
    )


def _int(value) -> int:
    try:
        return int(Decimal(str(value)))
    except Exception:
        return 0


def upsert_gmv_max_spend_daily(
    session,
    rows: list[dict],
    *,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    raw_response_id: Optional[int] = None,
) -> int:
    """把解析后的日级 GMV Max 行幂等 upsert。

    `rows` 每项来自 normalize.parse_gmv_max_report：需含 metric_date(date)、currency、
    cost / net_cost / gross_revenue / roi(Decimal)、orders。metric_date 为 None 的行
    （无按天维度）跳过——本表按天存。按 scope_key first()：有则逐字段更新、无则 add；末尾 flush。
    """
    from models.base_models import FactGmvMaxSpendDaily

    written = 0
    for row in rows:
        metric_date = row.get("metric_date")
        if metric_date is None:
            continue
        scope_key = build_gmv_max_scope_key(
            metric_date=metric_date,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
        )
        existing = (
            session.query(FactGmvMaxSpendDaily)
            .filter_by(scope_key=scope_key)
            .first()
        )
        if existing:
            existing.currency = row.get("currency")
            existing.cost = row["cost"]
            existing.net_cost = row["net_cost"]
            existing.gross_revenue = row["gross_revenue"]
            existing.orders = _int(row.get("orders"))
            existing.roi = row["roi"]
            existing.raw_response_id = raw_response_id
        else:
            session.add(
                FactGmvMaxSpendDaily(
                    metric_date=metric_date,
                    platform=PLATFORM,
                    country=country,
                    shop_id=shop_id,
                    seller_id=seller_id,
                    account_id=account_id,
                    scope_key=scope_key,
                    currency=row.get("currency"),
                    cost=row["cost"],
                    net_cost=row["net_cost"],
                    gross_revenue=row["gross_revenue"],
                    orders=_int(row.get("orders")),
                    roi=row["roi"],
                    raw_response_id=raw_response_id,
                )
            )
        written += 1
    session.flush()
    return written
