"""预估利润卡取数（阶段3a）：从 fact_profit_daily 按 profit_kind 聚合两套（预估/真实）。

供 web/routes/data.py 的 /profit/summary 端点与 board._collect 复用。预估行(estimated)由
flows/aggregate_profit 预先写好；真实行(settled)属 3b 回填，本期通常为 None。无数据 → available=False
（前端优雅降级，照 channel_metrics 范式），不抛错、不返 503。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from core.config import settings
from core.db import SessionLocal
from core.timezone import business_today
from analytics.profit_alerts import ProfitRecordInput, calculate_gross_profit
from models.base_models import DailyProfit
from services.profit_aggregation import compute_daily_profit

_SUM_COLUMNS = (
    "gmv", "gross_profit", "commission_fee", "ad_cost",
    "product_cost", "refund_amount", "order_count", "units_sold",
)


def _f(value) -> float:
    if value is None:
        return 0.0
    return float(value)


def _pack(row) -> dict:
    gmv = _f(row.gmv)
    gross = _f(row.gross_profit)
    return {
        "gmv": gmv,
        "gross_profit": gross,
        "commission_fee": _f(row.commission_fee),
        "ad_cost": _f(row.ad_cost),
        "product_cost": _f(row.product_cost),
        "refund_amount": _f(row.refund_amount),
        "order_count": int(row.order_count or 0),
        "units_sold": int(row.units_sold or 0),
        "profit_margin": round(gross / gmv * 100, 1) if gmv else None,
    }


def _empty_pack() -> dict:
    return {
        "gmv": 0.0,
        "gross_profit": 0.0,
        "commission_fee": 0.0,
        "ad_cost": 0.0,
        "product_cost": 0.0,
        "refund_amount": 0.0,
        "order_count": 0,
        "units_sold": 0,
        "profit_margin": None,
    }


def _pack_record(record: ProfitRecordInput) -> dict:
    return {
        "gmv": float(record.gmv),
        "gross_profit": float(calculate_gross_profit(record)),
        "commission_fee": float(record.commission_fee),
        "ad_cost": float(record.ad_cost),
        "product_cost": float(record.product_cost),
        "refund_amount": float(record.refund_amount),
        "order_count": int(record.order_count or 0),
        "units_sold": int(record.units_sold or 0),
        "profit_margin": None,
    }


def _add_pack(left: Optional[dict], right: dict) -> dict:
    result = dict(left or _empty_pack())
    for key in _SUM_COLUMNS:
        result[key] = result.get(key, 0) + right.get(key, 0)
    gmv = result["gmv"]
    gross = result["gross_profit"]
    result["profit_margin"] = round(gross / gmv * 100, 1) if gmv else None
    return result


def _today_estimated_pack(
    *,
    metric_date: date,
    platform: Optional[str],
    country: Optional[str],
    shop_ids: Optional[list[str]],
    account_id: Optional[str],
    session,
) -> dict:
    """实时重算今天的 estimated 利润，避免读到日表中过期的盘中快照。"""
    platform = platform or "tiktok_shop"
    country = country or "GLOBAL"
    shops = shop_ids or [None]
    total: Optional[dict] = None
    for shop_id in shops:
        record = compute_daily_profit(
            metric_date=metric_date,
            platform=platform,
            country=country,
            shop_id=shop_id,
            account_id=account_id,
            session=session,
        )
        total = _add_pack(total, _pack_record(record))
    return total or _empty_pack()


def get_profit_card(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    account_id: Optional[str] = None,
    session=None,
) -> dict:
    """返回 {available, currency, estimated:{...}|None, settled:{...}|None}（窗口聚合）。"""
    own = session is None
    session = session or SessionLocal()
    try:
        today = business_today()
        includes_today = start_date <= today <= end_date
        historical_end = min(end_date, today - timedelta(days=1)) if includes_today else end_date

        query = session.query(
            DailyProfit.profit_kind,
            *[func.coalesce(func.sum(getattr(DailyProfit, c)), 0).label(c) for c in _SUM_COLUMNS],
        ).filter(
            DailyProfit.metric_date >= start_date,
            DailyProfit.metric_date <= historical_end,
        )
        if platform:
            query = query.filter(DailyProfit.platform == platform)
        if country:
            query = query.filter(DailyProfit.country == country)
        if shop_ids:
            query = query.filter(DailyProfit.shop_id.in_(shop_ids))
        query = query.group_by(DailyProfit.profit_kind)

        by_kind = {row.profit_kind: _pack(row) for row in query.all()}
        estimated = by_kind.get("estimated")
        settled = by_kind.get("settled")
        if includes_today:
            today_estimated = _today_estimated_pack(
                metric_date=today,
                platform=platform,
                country=country,
                shop_ids=shop_ids,
                account_id=account_id,
                session=session,
            )
            estimated = _add_pack(estimated, today_estimated)

        # 覆盖天数护栏：预聚合表缺天会静默少算（aggregate_profit 漏跑/未回填）。统计 estimated
        # 行在窗口内实际覆盖的不同业务日数,与期望天数对比,让"数据不全"在前端可见。今天实时重算,
        # 不要求 fact_profit_daily 已有今日行。
        cov_q = session.query(
            func.count(func.distinct(DailyProfit.metric_date))
        ).filter(
            DailyProfit.metric_date >= start_date,
            DailyProfit.metric_date <= historical_end,
            DailyProfit.profit_kind == "estimated",
        )
        if platform:
            cov_q = cov_q.filter(DailyProfit.platform == platform)
        if country:
            cov_q = cov_q.filter(DailyProfit.country == country)
        if shop_ids:
            cov_q = cov_q.filter(DailyProfit.shop_id.in_(shop_ids))
        covered_days = int(cov_q.scalar() or 0)
        if includes_today:
            covered_days += 1
        expected_days = (end_date - start_date).days + 1

        return {
            "available": estimated is not None,
            "currency": settings.profit_currency,
            "estimated": estimated,
            "settled": settled,
            "expected_days": expected_days,
            "covered_days": covered_days,
            "coverage_complete": covered_days >= expected_days,
        }
    finally:
        if own:
            session.close()
