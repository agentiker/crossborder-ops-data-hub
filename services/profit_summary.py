"""预估利润卡取数（阶段3a）：从 fact_profit_daily 按 profit_kind 聚合两套（预估/真实）。

供 web/routes/data.py 的 /profit/summary 端点与 board._collect 复用。预估行(estimated)由
flows/aggregate_profit 预先写好；真实行(settled)属 3b 回填，本期通常为 None。无数据 → available=False
（前端优雅降级，照 channel_metrics 范式），不抛错、不返 503。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from core.config import settings
from core.db import SessionLocal
from models.base_models import DailyProfit

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


def get_profit_card(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    session=None,
) -> dict:
    """返回 {available, currency, estimated:{...}|None, settled:{...}|None}（窗口聚合）。"""
    own = session is None
    session = session or SessionLocal()
    try:
        query = session.query(
            DailyProfit.profit_kind,
            *[func.coalesce(func.sum(getattr(DailyProfit, c)), 0).label(c) for c in _SUM_COLUMNS],
        ).filter(
            DailyProfit.metric_date >= start_date,
            DailyProfit.metric_date <= end_date,
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
        return {
            "available": estimated is not None,
            "currency": settings.profit_currency,
            "estimated": estimated,
            "settled": settled,
        }
    finally:
        if own:
            session.close()
