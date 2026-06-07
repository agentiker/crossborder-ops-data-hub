"""Data query API endpoints for AI assistants and integrations."""

import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

from core.db import SessionLocal
from models.base_models import Inventory, Product
from services.order_metrics import get_gmv_summary, get_gmv_trend, get_top_skus

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


class ProductItemOut(BaseModel):
    product_id: str
    title: Optional[str] = None
    status: Optional[str] = None
    sales_regions: Optional[list[str]] = None
    sku_count: int = 0
    min_price: Optional[float] = None
    currency: Optional[str] = None


class ProductResponse(BaseModel):
    items: list[ProductItemOut]
    total: int


class TrendPoint(BaseModel):
    date: str
    gmv: float
    order_count: int
    units_sold: int


class TrendResponse(BaseModel):
    start_date: str
    end_date: str
    points: list[TrendPoint]


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


PROFIT_NOT_READY = (
    "利润功能规划中：需先接入结算(Finance API)、广告费(Ads API)与商品成本录入后开放。"
)
ALERTS_NOT_READY = (
    "告警功能规划中：依赖利润与库存指标，待结算/广告/成本数据接入后开放。"
)


@router.get("/profit/summary", response_model=ProfitSummary)
async def get_profit(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
):
    """利润汇总（规划中，本期不提供）。

    计算逻辑已就绪，但缺成本数据源（结算/广告/商品成本），返回数据会误导，故显式 503。
    """
    raise HTTPException(status_code=503, detail=PROFIT_NOT_READY)


@router.get("/alerts", response_model=AlertResponse)
async def get_alerts(
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
    limit: int = Query(20, description="返回数量"),
):
    """未处理告警（规划中，本期不提供）。"""
    raise HTTPException(status_code=503, detail=ALERTS_NOT_READY)


@router.get("/overview")
async def get_overview(
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
):
    """经营概览：库存快照 + 近 7 天订单概览（不含利润/告警，本期未上线）。"""
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

    # 近 7 天订单（已付款口径）
    orders = get_gmv_summary(
        start_date=week_ago,
        end_date=today,
        platform=platform,
        country=country,
        shop_id=shop_id,
    )

    return {
        "period": f"{week_ago.isoformat()} ~ {today.isoformat()}",
        "inventory": {
            "total_sku": total_sku,
            "total_stock": total_stock,
            "low_stock_count": low_stock,
        },
        "orders": {
            "gmv": orders["gmv"],
            "order_count": orders["order_count"],
            "units_sold": orders["units_sold"],
            "avg_order_value": orders["avg_order_value"],
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


@router.get("/orders/trend", response_model=TrendResponse)
async def get_orders_trend(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID（店铺 GMV 趋势按此过滤）"),
):
    """已付款订单按天的 GMV/单量/销量趋势，默认近 7 天（窗口内无单的日期补 0）。

    近 3 天/7 天传不同 start_date；店铺 GMV 趋势传 shop_id。
    """
    today = date.today()
    sd = date.fromisoformat(start_date) if start_date else today - timedelta(days=6)
    ed = date.fromisoformat(end_date) if end_date else today

    points = get_gmv_trend(
        start_date=sd,
        end_date=ed,
        platform=platform,
        country=country,
        shop_id=shop_id,
    )
    return TrendResponse(
        start_date=sd.isoformat(),
        end_date=ed.isoformat(),
        points=[TrendPoint(**p) for p in points],
    )


@router.get("/products", response_model=ProductResponse)
async def get_products(
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
    status: Optional[str] = Query(None, description="商品状态，如 ACTIVATE / SELLER_DEACTIVATED / DRAFT"),
    limit: int = Query(100, description="返回数量上限"),
):
    """商品目录列表，支持平台/国家/店铺/状态过滤，用于商品目录、上下架与滞销分析。"""
    session = SessionLocal()
    try:
        query = session.query(Product)
        if platform:
            query = query.filter(Product.platform == platform)
        if country:
            query = query.filter(Product.country == country)
        if shop_id:
            query = query.filter(Product.shop_id == shop_id)
        if status:
            query = query.filter(Product.status == status)
        rows = query.order_by(Product.source_update_time.desc()).limit(limit).all()
        items = [
            ProductItemOut(
                product_id=r.product_id,
                title=r.title,
                status=r.status,
                sales_regions=r.sales_regions,
                sku_count=r.sku_count or 0,
                min_price=float(r.min_price) if r.min_price is not None else None,
                currency=r.currency,
            )
            for r in rows
        ]
        return ProductResponse(items=items, total=len(items))
    finally:
        session.close()
