"""结算真实利润回填 flow（阶段3b）：对结算完整的历史天，用纯真实结算数据回填 settled 利润行。

与 aggregate_profit（每日预估 estimated）解耦、对称：本 flow 只处理**结算已完整**的历史天，
写 profit_kind=settled 行（与同日同店的 estimated 行并存、互不覆盖，scope_key 已含 profit_kind）。

「结算完整」的判定（两条同时满足）：
1. metric_date ≤ business_today − settle_lag_days（结算滞后窗口之外，见 fee_rate_settle_lag_days=14）
2. 该店该天未结算行已清零（unsettled_open_count == 0）——订单已全部结算，无预估成分残留

settled 利润 = GMV − 真实结算扣点 − 真实结算广告 − 产品成本 − **真实退货金额**（付款后取消
sub_total）。与 estimated 的差异 = 预估费用/退货 vs 真实。GMV 为 0 的天跳过（无意义空行）。

默认扫描 [today−settle_lag−lookback, today−settle_lag] 区间，逐天逐店回填（幂等 upsert）。
挂 timer 每日凌晨在 estimated 聚合后跑。回填历史用 --days / --date。
"""

import logging
from datetime import timedelta
from typing import Optional

from core.retry import retry

logger = logging.getLogger(__name__)

from core.config import settings
from core.db import SessionLocal
from core.timezone import business_today
from platforms.tiktok_shop.client import PLATFORM as TIKTOK_PLATFORM
from services.metrics_store import upsert_daily_profit
from services.profit_aggregation import compute_daily_profit, unsettled_open_count

# 每次 timer 触发额外回看的天数：settle_lag 线往前再扫这么多天，兜住迟到结算/漏跑。
_DEFAULT_LOOKBACK_DAYS = 7


@retry(retries=2, delay_seconds=30)
def backfill_one(
    *,
    target_date,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> Optional[dict]:
    """回填单店单日 settled 利润。结算未完整 / GMV 为 0 时跳过（返回 None）。"""
    session = SessionLocal()
    try:
        open_cnt = unsettled_open_count(
            session, target_date, platform=TIKTOK_PLATFORM, country=country,
            shop_id=shop_id, seller_id=seller_id, account_id=account_id,
        )
        if open_cnt > 0:
            logger.info(
                "settled 回填跳过 %s shop=%s：未结算仍有 %d 行（结算未完整）",
                target_date, shop_id, open_cnt,
            )
            return None

        record = compute_daily_profit(
            metric_date=target_date,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            kind="settled",
            session=session,
        )
        if record.gmv == 0:
            logger.info("settled 回填跳过 %s shop=%s：GMV=0（无数据）", target_date, shop_id)
            return None

        row = upsert_daily_profit(session, record)
        session.commit()
        return {"gmv": float(row.gmv), "gross_profit": float(row.gross_profit)}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def backfill_settled_profit_flow(
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    target_date=None,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
):
    """结算真实利润回填主流程。

    target_date=None（timer 行为）：扫 [settle_line−lookback, settle_line] 区间逐日回填，
    settle_line = business_today − settle_lag_days。target_date 指定时只回填该天。
    """
    settle_lag = settings.fee_rate_settle_lag_days
    if target_date is not None:
        dates = [target_date]
    else:
        settle_line = business_today() - timedelta(days=settle_lag)
        dates = [settle_line - timedelta(days=i) for i in range(lookback_days + 1)]

    filled = 0
    for d in dates:
        result = backfill_one(
            target_date=d,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
        )
        if result is not None:
            filled += 1
            print(
                f"settled 回填 {d} shop={shop_id}: "
                f"GMV={result['gmv']:.2f} 真实利润={result['gross_profit']:.2f} CNY"
            )
    print(f"settled 回填完成 shop={shop_id}: {filled}/{len(dates)} 天已写")
    return {"filled": filled, "scanned": len(dates)}


if __name__ == "__main__":
    import argparse
    from datetime import date as _date
    from functools import partial

    from flows._shop_discovery import run_for_all_shops

    parser = argparse.ArgumentParser(
        description="结算真实利润回填。无参数=扫结算完整线附近区间（timer 行为）；"
                    "历史回填用 --days 或 --date。"
    )
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--days", type=int, metavar="N",
                   help="从结算完整线往前回看 N 天（覆盖 timer 默认的 lookback）")
    g.add_argument("--date", type=str, metavar="YYYY-MM-DD",
                   help="只回填指定业务日（结算未完整则跳过）")
    args = parser.parse_args()

    if args.date:
        target = _date.fromisoformat(args.date)
        run_for_all_shops(partial(backfill_settled_profit_flow, target_date=target))
    elif args.days:
        run_for_all_shops(partial(backfill_settled_profit_flow, lookback_days=args.days))
    else:
        run_for_all_shops(backfill_settled_profit_flow)
