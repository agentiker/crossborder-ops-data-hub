"""广告消耗取数 + ROAS（结算口径）。

数据源 fact_ad_spend_daily（由 flows.sync_ad_spend 落库，按印尼业务日 + currency 聚合）。
参数规格与过滤写法对齐 services.order_metrics.get_gmv_summary，维度统一（platform/country/shop_ids）。
ROAS = GMV ÷ 广告消耗；广告费为 0 → roas=None（不臆造）。金额内部 Decimal，输出转 float。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from core.db import SessionLocal
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
) -> dict:
    """窗口 [start_date, end_date]（按 metric_date）内广告消耗三项 + 总额聚合。

    口径：结算口径（fact_ad_spend_daily 按印尼业务日）。currency 取窗口内任一非空值
    （单店单币种常见；多币种混合时仅作展示用，不做换算）。
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

        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "total_ad_spend": _to_float(total_ad_spend),
            "gmv_max_fee": _to_float(gmv_max_fee),
            "tap_commission": _to_float(tap),
            "affiliate_commission": _to_float(affiliate),
            "currency": currency,
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
) -> dict:
    """ROAS = GMV ÷ 广告消耗。广告费为 0 → roas=None（不臆造）。

    GMV 复用 services.order_metrics.get_gmv_summary（已付款口径，维度对齐）；
    广告消耗用本模块 get_ad_spend_summary（结算口径）。两者口径不同（成交 vs 结算），
    ROAS 仅作参考。
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
    )
    gmv = gmv_summary["gmv"]
    ad_spend = spend_summary["total_ad_spend"]
    roas = round(gmv / ad_spend, 2) if ad_spend else None

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "gmv": gmv,
        "ad_spend": ad_spend,
        "roas": roas,
        "gmv_max_fee": spend_summary["gmv_max_fee"],
        "tap_commission": spend_summary["tap_commission"],
        "affiliate_commission": spend_summary["affiliate_commission"],
        "currency": spend_summary["currency"],
    }
