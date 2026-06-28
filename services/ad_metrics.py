"""广告消耗取数 + ROAS（结算口径）。

数据源 fact_ad_spend_daily（由 flows.sync_ad_spend 落库，按印尼业务日 + currency 聚合）。
参数规格与过滤写法对齐 services.order_metrics.get_gmv_summary，维度统一（platform/country/shop_ids）。
ROAS = GMV ÷ 广告消耗；广告费为 0 → roas=None（不臆造）。金额内部 Decimal，输出转 float。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from core.config import settings
from core.db import SessionLocal
from core.timezone import business_today
from models.base_models import FactAdSpendDaily
from services.order_metrics import get_gmv_summary


def _to_float(value) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value or 0)


def _scope_filters(query, model, platform, country, shop_ids=None):
    if platform:
        query = query.filter(model.platform == platform)
    if country:
        query = query.filter(model.country == country)
    if shop_ids:
        query = query.filter(model.shop_id.in_(shop_ids))
    return query


def get_ad_spend_summary(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    as_of: Optional[date] = None,
) -> dict:
    """窗口 [start_date, end_date]（按 metric_date）内营销支出三项 + 拆分口径 + 覆盖护栏。

    口径：结算口径（fact_ad_spend_daily 按印尼业务日）。currency 取窗口内任一非空值
    （单店单币种常见；多币种混合时仅作展示用，不做换算）。

    ⚠️ **两类口径分开**（2026-06-28，真打 prod 揭示）：把成交才付的达人佣金当广告投放算 ROAS
    会误导（见 docs/business-rules §6）。站内三项里只有 GMV Max 是付费投放，TAP / 联盟都是达人佣金：
    - **付费投放**（`paid_ad_spend` = **仅 GMV Max**）：预算型/撬动型投放（设预算买曝光），ROAS 只该
      对它算。
    - **达人带货佣金**（`creator_commission` = **TAP + 联盟**，均 CPS）：成交后按比例分佣，跟着 GMV
      走、无撬动（TAP=TikTok Affiliate Partner 机构代管达人，字段名带 "ads" 但本质是佣金；联盟=开放
      达人计划）。拿它当广告投入算 ROAS = 佣金率倒数，无意义。

    **覆盖护栏**（`complete` / `settled_through`）：广告费来自**已结算** statement，且
    fact_ad_spend_daily 按 **order_create_time** 归日 → 近 `ad_settle_lag_days` 天下单的单多未结算、
    广告费仍在**持续填充**（叠加同步 timer 未跑则更滞后）。故护栏不看"有没有数据"（近期日有数据但
    不全会误判完整），而看**结算完整线** `settled_through = as_of − ad_settle_lag_days`：窗口结束日
    晚于该线 → `complete=False`，前端标注「结算中」，避免把"结算未回"误读成"广告变高效/ROAS 暴涨"。
    `latest_covered_date` 仅作"数据截至"展示。
    """
    session = SessionLocal()
    try:
        query = session.query(
            func.coalesce(func.sum(FactAdSpendDaily.total_ad_spend), 0),
            func.coalesce(func.sum(FactAdSpendDaily.gmv_max_fee), 0),
            func.coalesce(func.sum(FactAdSpendDaily.tap_commission), 0),
            func.coalesce(func.sum(FactAdSpendDaily.affiliate_commission), 0),
            func.max(FactAdSpendDaily.currency),
        ).filter(
            FactAdSpendDaily.metric_date >= start_date,
            FactAdSpendDaily.metric_date <= end_date,
        )
        query = _scope_filters(query, FactAdSpendDaily, platform, country, shop_ids)
        total_ad_spend, gmv_max_fee, tap, affiliate, currency = query.one()

        latest_q = session.query(func.max(FactAdSpendDaily.metric_date))
        latest_q = _scope_filters(latest_q, FactAdSpendDaily, platform, country, shop_ids)
        latest_covered = latest_q.scalar()

        # 结算完整线：今天 − 结算滞后天数。窗口结束日晚于该线 = 末尾几天广告费还在结算填充 → 不完整
        as_of_d = as_of or business_today()
        settled_through = as_of_d - timedelta(days=settings.ad_settle_lag_days)
        complete = end_date <= settled_through

        gmv_max_f = _to_float(gmv_max_fee)
        tap_f = _to_float(tap)
        affiliate_f = _to_float(affiliate)
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "total_ad_spend": _to_float(total_ad_spend),
            "paid_ad_spend": gmv_max_f,                # 付费投放 = 仅 GMV Max（预算撬动型，ROAS 用此口径）
            "creator_commission": tap_f + affiliate_f,  # 达人带货佣金 = TAP + 联盟（均 CPS 成交分佣）
            "gmv_max_fee": gmv_max_f,
            "tap_commission": tap_f,
            "affiliate_commission": affiliate_f,
            "currency": currency,
            "latest_covered_date": latest_covered.isoformat() if latest_covered else None,
            "settled_through": settled_through.isoformat(),
            "complete": complete,
        }
    finally:
        session.close()


def get_ad_spend_trend(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
) -> dict:
    """窗口 [start_date, end_date] 内按印尼业务日（metric_date）聚合的广告消耗序列。

    口径同 get_ad_spend_summary（结算口径）。仅返回有数据的业务日，缺失日由调用方
    按订单趋势的日期轴对齐补 0（见 web/routes/report.py _collect）。
    """
    session = SessionLocal()
    try:
        query = session.query(
            FactAdSpendDaily.metric_date,
            func.coalesce(func.sum(FactAdSpendDaily.total_ad_spend), 0),
        ).filter(
            FactAdSpendDaily.metric_date >= start_date,
            FactAdSpendDaily.metric_date <= end_date,
        )
        query = _scope_filters(query, FactAdSpendDaily, platform, country, shop_ids)
        query = query.group_by(FactAdSpendDaily.metric_date).order_by(
            FactAdSpendDaily.metric_date
        )
        points = [
            {"date": d.isoformat(), "total_ad_spend": _to_float(v)}
            for d, v in query.all()
        ]
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "points": points,
        }
    finally:
        session.close()


def get_roas(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    as_of: Optional[date] = None,
) -> dict:
    """ROAS = GMV ÷ **付费投放**（paid_ad_spend = 仅 GMV Max）。付费投放为 0 → roas=None（不臆造）。

    ⚠️ 分母用**付费投放**而非营销总支出（2026-06-28 口径修正，见 get_ad_spend_summary / docs §6）：
    达人佣金（TAP + 联盟，均 CPS）成交才付、跟着 GMV 走，算进 ROAS 分母会把"佣金率倒数"误当广告
    效率。达人主导、不投 GMV Max 的店付费投放为 0 → roas=None，诚实留空。

    GMV 复用 get_gmv_summary（已付款口径）；付费投放用 get_ad_spend_summary（结算口径）。两者
    口径不同（成交 vs 结算），且广告结算滞后 → 贴近今天的窗口 roas 偏高，调用方按 `complete` 标注。
    """
    gmv_summary = get_gmv_summary(
        start_date=start_date,
        end_date=end_date,
        platform=platform,
        country=country,
        shop_ids=shop_ids,
    )
    spend_summary = get_ad_spend_summary(
        start_date=start_date,
        end_date=end_date,
        platform=platform,
        country=country,
        shop_ids=shop_ids,
        as_of=as_of,
    )
    gmv = gmv_summary["gmv"]
    paid_ad_spend = spend_summary["paid_ad_spend"]
    roas = round(gmv / paid_ad_spend, 2) if paid_ad_spend else None

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "gmv": gmv,
        "ad_spend": spend_summary["total_ad_spend"],  # 营销总支出（含佣金），展示用
        "paid_ad_spend": paid_ad_spend,               # 付费投放 = 仅 GMV Max（ROAS 分母）
        "creator_commission": spend_summary["creator_commission"],  # 达人佣金 = TAP + 联盟
        "roas": roas,
        "gmv_max_fee": spend_summary["gmv_max_fee"],
        "tap_commission": spend_summary["tap_commission"],
        "affiliate_commission": spend_summary["affiliate_commission"],
        "currency": spend_summary["currency"],
        "latest_covered_date": spend_summary["latest_covered_date"],
        "settled_through": spend_summary["settled_through"],
        "complete": spend_summary["complete"],
    }
