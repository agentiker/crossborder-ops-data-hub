"""Data query API endpoints for AI assistants and integrations."""

import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

from ai_tools.operations_read import get_profit_summary, list_open_alerts
from core.db import SessionLocal
from models.base_models import Inventory, DailyProfit, Alert
from services.order_metrics import get_gmv_summary, get_top_skus
from sqlalchemy import func

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Response Models ──────────────────────────────────────────────────────────


class InventoryItem(BaseModel):
    sku_id: str
    product_id: str
    product_name: Optional[str] = None
    sku_name: Optional[str] = None
    available_stock: int = 0
    reserved_stock: int = 0
    warehouse_id: Optional[str] = None


class InventoryResponse(BaseModel):
    items: list[InventoryItem]
    total: int
    low_stock_items: list[InventoryItem]  # available_stock < 10


class ProfitSummary(BaseModel):
    start_date: str
    end_date: str
    gmv: float
    gross_profit: float
    ad_cost: float
    order_count: int
    units_sold: int
    profit_margin: float


class AlertItem(BaseModel):
    metric_date: Optional[str] = None
    alert_type: str
    severity: str
    title: str
    message: Optional[str] = None
    impact_scope: Optional[str] = None


class AlertResponse(BaseModel):
    alerts: list[AlertItem]
    total: int


class OrderSummary(BaseModel):
    start_date: str
    end_date: str
    gmv: float
    order_count: int
    units_sold: int
    avg_order_value: float


class TopSkuItem(BaseModel):
    sku_id: Optional[str] = None
    product_name: Optional[str] = None
    sku_name: Optional[str] = None
    units_sold: int
    gmv: float


class TopSkuResponse(BaseModel):
    items: list[TopSkuItem]
    total: int


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/inventory", response_model=InventoryResponse)
async def get_inventory(
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
    low_stock_threshold: int = Query(10, description="低库存阈值"),
):
    """获取库存列表，同时返回低库存商品"""
    session = SessionLocal()
    try:
        query = session.query(Inventory)
        if platform:
            query = query.filter(Inventory.platform == platform)
        if country:
            query = query.filter(Inventory.country == country)
        if shop_id:
            query = query.filter(Inventory.shop_id == shop_id)

        rows = query.all()
        items = [
            InventoryItem(
                sku_id=r.sku_id,
                product_id=r.product_id,
                product_name=r.product_name,
                sku_name=r.sku_name,
                available_stock=r.available_stock or 0,
                reserved_stock=r.reserved_stock or 0,
                warehouse_id=r.warehouse_id,
            )
            for r in rows
        ]
        low_stock = [i for i in items if i.available_stock < low_stock_threshold]

        return InventoryResponse(
            items=items,
            total=len(items),
            low_stock_items=low_stock,
        )
    finally:
        session.close()


@router.get("/profit/summary", response_model=ProfitSummary)
async def get_profit(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
):
    """获取利润汇总数据，默认最近7天"""
    today = date.today()
    sd = date.fromisoformat(start_date) if start_date else today - timedelta(days=7)
    ed = date.fromisoformat(end_date) if end_date else today

    data = get_profit_summary(
        start_date=sd,
        end_date=ed,
        platform=platform,
        country=country,
        shop_id=shop_id,
    )

    gmv = data["gmv"]
    gross_profit = data["gross_profit"]
    profit_margin = (gross_profit / gmv * 100) if gmv > 0 else 0

    return ProfitSummary(
        start_date=data["start_date"],
        end_date=data["end_date"],
        gmv=gmv,
        gross_profit=gross_profit,
        ad_cost=data["ad_cost"],
        order_count=data["order_count"],
        units_sold=data["units_sold"],
        profit_margin=round(profit_margin, 2),
    )


@router.get("/alerts", response_model=AlertResponse)
async def get_alerts(
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
    limit: int = Query(20, description="返回数量"),
):
    """获取未处理的告警"""
    alerts = list_open_alerts(
        platform=platform, country=country, shop_id=shop_id, limit=limit
    )
    return AlertResponse(
        alerts=[
            AlertItem(
                metric_date=a.get("metric_date"),
                alert_type=a["alert_type"],
                severity=a["severity"],
                title=a["title"],
                message=a.get("message"),
                impact_scope=a.get("impact_scope"),
            )
            for a in alerts
        ],
        total=len(alerts),
    )


@router.get("/overview")
async def get_overview(
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
):
    """获取经营概览（库存+利润+告警的综合视图）"""
    today = date.today()
    week_ago = today - timedelta(days=7)

    # 库存
    session = SessionLocal()
    try:
        inv_query = session.query(Inventory)
        if platform:
            inv_query = inv_query.filter(Inventory.platform == platform)
        if country:
            inv_query = inv_query.filter(Inventory.country == country)
        if shop_id:
            inv_query = inv_query.filter(Inventory.shop_id == shop_id)
        inv_rows = inv_query.all()
        total_sku = len(inv_rows)
        low_stock = sum(1 for r in inv_rows if (r.available_stock or 0) < 10)
        total_stock = sum(r.available_stock or 0 for r in inv_rows)
    finally:
        session.close()

    # 利润
    profit = get_profit_summary(
        start_date=week_ago,
        end_date=today,
        platform=platform,
        country=country,
        shop_id=shop_id,
    )

    # 告警
    alerts = list_open_alerts(
        platform=platform, country=country, shop_id=shop_id, limit=5
    )

    return {
        "period": f"{week_ago.isoformat()} ~ {today.isoformat()}",
        "inventory": {
            "total_sku": total_sku,
            "total_stock": total_stock,
            "low_stock_count": low_stock,
        },
        "profit": {
            "gmv": profit["gmv"],
            "gross_profit": profit["gross_profit"],
            "order_count": profit["order_count"],
            "units_sold": profit["units_sold"],
        },
        "alerts": {
            "total": len(alerts),
            "critical": sum(1 for a in alerts if a["severity"] == "critical"),
            "items": alerts[:5],
        },
    }


@router.get("/orders/summary", response_model=OrderSummary)
async def get_orders_summary(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
):
    """已付款订单 GMV/订单量/销量/客单价汇总，默认最近7天（按 paid_time 归日）。"""
    today = date.today()
    sd = date.fromisoformat(start_date) if start_date else today - timedelta(days=7)
    ed = date.fromisoformat(end_date) if end_date else today

    data = get_gmv_summary(
        start_date=sd,
        end_date=ed,
        platform=platform,
        country=country,
        shop_id=shop_id,
    )
    return OrderSummary(**data)


@router.get("/orders/top-skus", response_model=TopSkuResponse)
async def get_orders_top_skus(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
    limit: int = Query(10, description="返回数量"),
):
    """已付款订单内按销量排序的单品榜，默认最近7天。"""
    today = date.today()
    sd = date.fromisoformat(start_date) if start_date else today - timedelta(days=7)
    ed = date.fromisoformat(end_date) if end_date else today

    items = get_top_skus(
        start_date=sd,
        end_date=ed,
        platform=platform,
        country=country,
        shop_id=shop_id,
        limit=limit,
    )
    return TopSkuResponse(
        items=[TopSkuItem(**i) for i in items],
        total=len(items),
    )
