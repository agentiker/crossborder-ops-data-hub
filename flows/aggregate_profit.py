"""预估利润聚合 flow（阶段3a）：按业务日聚合各店预估利润，写 fact_profit_daily。

默认聚合「前一日」印尼业务日（供今早出昨日预估利润）。单事务：compute_daily_profit →
upsert_daily_profit（profit_kind=estimated）。与 sync_unsettled_fees / sync_ad_spend 解耦，
依赖它们已落库（unsettled 预估 + 结算真实）。
"""

import logging
from datetime import timedelta
from typing import Optional

from core.retry import retry

logger = logging.getLogger(__name__)

from core.db import SessionLocal
from core.timezone import business_today
from platforms.tiktok_shop.client import PLATFORM as TIKTOK_PLATFORM
from services.metrics_store import upsert_daily_profit
from services.profit_aggregation import compute_daily_profit


@retry(retries=2, delay_seconds=30)
def aggregate_one(
    *,
    target_date,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> dict:
    """聚合单店单日预估利润并 upsert。返回 {gmv, gross_profit} 供日志。"""
    session = SessionLocal()
    try:
        record = compute_daily_profit(
            metric_date=target_date,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            session=session,
        )
        row = upsert_daily_profit(session, record)
        session.commit()
        return {"gmv": float(row.gmv), "gross_profit": float(row.gross_profit)}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def aggregate_profit_flow(
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    target_date=None,
):
    """预估利润聚合主流程。target_date 默认前一日（印尼业务日）。"""
    if target_date is None:
        target_date = business_today() - timedelta(days=1)
    result = aggregate_one(
        target_date=target_date,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    print(
        f"预估利润聚合完成 {target_date} shop={shop_id}: "
        f"GMV={result['gmv']:.2f} 预估利润={result['gross_profit']:.2f} CNY"
    )
    return result


if __name__ == "__main__":
    from flows._shop_discovery import run_for_all_shops

    run_for_all_shops(aggregate_profit_flow)
