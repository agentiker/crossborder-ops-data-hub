"""Read-only operations summaries for AI assistants."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from core.db import SessionLocal
from models.base_models import Alert, DailyProfit


def get_profit_summary(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
) -> dict:
    """Return trusted profit aggregates for AI explanation."""
    session = SessionLocal()
    try:
        query = session.query(
            func.coalesce(func.sum(DailyProfit.gmv), 0),
            func.coalesce(func.sum(DailyProfit.gross_profit), 0),
            func.coalesce(func.sum(DailyProfit.ad_cost), 0),
            func.coalesce(func.sum(DailyProfit.order_count), 0),
            func.coalesce(func.sum(DailyProfit.units_sold), 0),
        ).filter(
            DailyProfit.metric_date >= start_date,
            DailyProfit.metric_date <= end_date,
        )
        if platform:
            query = query.filter(DailyProfit.platform == platform)
        if country:
            query = query.filter(DailyProfit.country == country)
        if shop_id:
            query = query.filter(DailyProfit.shop_id == shop_id)

        gmv, gross_profit, ad_cost, order_count, units_sold = query.one()
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "gmv": _to_float(gmv),
            "gross_profit": _to_float(gross_profit),
            "ad_cost": _to_float(ad_cost),
            "order_count": int(order_count or 0),
            "units_sold": int(units_sold or 0),
        }
    finally:
        session.close()


def list_open_alerts(
    *,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Return open business alerts in a narrow, read-only shape."""
    session = SessionLocal()
    try:
        query = session.query(Alert).filter(Alert.status == "open")
        if platform:
            query = query.filter(Alert.platform == platform)
        if country:
            query = query.filter(Alert.country == country)
        if shop_id:
            query = query.filter(Alert.shop_id == shop_id)
        rows = query.order_by(Alert.created_at.desc()).limit(limit).all()
        return [
            {
                "metric_date": row.metric_date.isoformat() if row.metric_date else None,
                "platform": row.platform,
                "country": row.country,
                "shop_id": row.shop_id,
                "alert_type": row.alert_type,
                "severity": row.severity,
                "title": row.title,
                "message": row.message,
                "impact_scope": row.impact_scope,
                "payload": row.payload,
            }
            for row in rows
        ]
    finally:
        session.close()


def _to_float(value) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value or 0)
