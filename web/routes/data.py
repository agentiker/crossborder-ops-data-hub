"""Data query API endpoints for AI assistants and integrations."""

import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

from core.config import settings
from core.db import SessionLocal
from core.timezone import PERIOD_KEYS, business_today, describe_window, resolve_period
from models.base_models import Inventory, Product
from services.fulfillment_metrics import get_pending_fulfillments
from services.order_metrics import get_gmv_summary, get_gmv_trend, get_top_skus
from services.scope_binding import get_binding, set_binding
from services.scope_resolution import ScopeError, ScopeFilters, list_scopes, resolve_filters
from services.stock_metrics import get_stock_risk
from web.signed_link import make_token

logger = logging.getLogger(__name__)
router = APIRouter()


def _resolve_scope(
    *,
    scope_id: Optional[str] = None,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[str] = None,
    open_id: Optional[str] = None,
) -> ScopeFilters:
    """统一解析 scope/显式过滤，ScopeError → 400。

    `scope_id` 对外命名，内部即 scope_key；`shop_ids` 为逗号分隔字符串。

    服务器端自动兜底：agent 没传 scope_id 但有 open_id 时，自动查 binding 表取默认范围，
    消除对弱模型「主动读取默认范围」的依赖。带 scope_id 则显式优先、不读 binding。
    读取走服务端默认 channel/account_id，与写端点 (ops_set_scope_binding) 默认完全一致，
    保证读写命中同一 binding 行（账号隔离靠 open_id 的 per-app 唯一性，见 SetScopeBindingRequest）。
    """
    if not scope_id and open_id:
        binding = get_binding(open_id)
        if binding.get("is_set") and binding.get("scope_key"):
            scope_id = binding["scope_key"]
            logger.info("auto-applied scope binding: open_id=%s → %s", open_id, scope_id)

    id_list = [s.strip() for s in shop_ids.split(",") if s.strip()] if shop_ids else None
    try:
        return resolve_filters(
            scope_key=scope_id,
            platform=platform,
            country=country,
            shop_id=shop_id,
            shop_ids=id_list,
        )
    except ScopeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _resolve_window(start_date, end_date, period, default_back_days):
    """统一窗口解析（按印尼时区）：显式 start/end > period 相对词 > 默认近 N 天。返回 (sd, ed)。

    把"今天/本周"等相对时间的换算放服务端（resolve_period，周一起算），不让 LLM 自己算日期，
    避免弱模型算错星期。period 无效 → 400。
    """
    today = business_today()
    if start_date or end_date:
        sd = date.fromisoformat(start_date) if start_date else today - timedelta(days=default_back_days)
        ed = date.fromisoformat(end_date) if end_date else today
        return sd, ed
    if period:
        try:
            return resolve_period(period)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return today - timedelta(days=default_back_days), today


# ── 数据口径常量（随响应 caliber 字段下发，agent 直接复述，无需在 skill 散文里背） ──
ORDERS_CALIBER = (
    "已付款订单口径（paid_time 非空、排除未付款/已取消，按 paid_time 归日，印尼当地时间 UTC+7）；"
    "GMV=订单 total_amount（买家实付，含运费税优惠，非平台结算）；"
    "销量=line_item 条数；客单价=GMV/订单数；来源 TikTok /order/202309/orders/search"
)
TOP_SKUS_CALIBER = (
    "已付款订单口径（统计窗口按印尼当地时间 UTC+7）；单品 GMV=该 SKU 各 line_item 的 sale_price 之和"
    "（商品行售价，不含运费）；排序按销量（line_item 条数）降序"
)
FULFILLMENTS_CALIBER = (
    "待发货快照口径（order_status=AWAITING_SHIPMENT，来源 TikTok /order/202309/orders/search 全量快照，"
    "非历史窗口、无相对时间参数）；超时(overdue)=已过平台发货截止时间(tts_sla_time)；"
    "临界(critical)=距截止不足 warning_hours（默认 24）小时；正常(normal)=距截止仍 ≥ warning_hours；"
    "未知(unknown)=无发货截止时间；所有时间为印尼当地时间 UTC+7；数据新鲜度见 snapshot_at"
)


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
    scope: Optional[str] = None  # 本次查询范围，如 "TikTok Shop / 印尼 / 3 个店铺"


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
    window_label: Optional[str] = None
    gmv: float
    order_count: int
    units_sold: int
    avg_order_value: float
    scope: Optional[str] = None
    caliber: Optional[str] = None


class TopSkuItem(BaseModel):
    sku_id: Optional[str] = None
    product_name: Optional[str] = None
    sku_name: Optional[str] = None
    units_sold: int
    gmv: float


class TopSkuResponse(BaseModel):
    items: list[TopSkuItem]
    total: int
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    window_label: Optional[str] = None
    scope: Optional[str] = None
    caliber: Optional[str] = None


class PendingFulfillmentItem(BaseModel):
    order_id: str
    shop_id: Optional[str] = None
    order_status: Optional[str] = None
    delivery_option_name: Optional[str] = None
    item_count: int = 0
    first_product_name: Optional[str] = None
    total_amount: float = 0.0
    currency: Optional[str] = None
    is_cod: bool = False
    create_time_local: Optional[str] = None  # 印尼当地时间 UTC+7
    sla_time_local: Optional[str] = None  # 发货截止时间，印尼当地 UTC+7
    hours_left: Optional[float] = None  # 距截止小时数（已超时为负）
    bucket: str  # overdue / critical / normal / unknown


class FulfillmentBuckets(BaseModel):
    overdue: int = 0
    critical: int = 0
    normal: int = 0
    unknown: int = 0
    total: int = 0


class ShopFulfillmentBucket(BaseModel):
    shop_id: str
    overdue: int = 0
    critical: int = 0
    normal: int = 0
    unknown: int = 0
    total: int = 0


class PendingFulfillmentsResponse(BaseModel):
    items: list[PendingFulfillmentItem]
    buckets: FulfillmentBuckets
    by_shop: list[ShopFulfillmentBucket]
    snapshot_at: Optional[str] = None  # 快照同步时间，印尼当地 UTC+7
    warning_hours: int = 24
    scope: Optional[str] = None
    caliber: Optional[str] = None


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
    scope: Optional[str] = None


class ScopeItem(BaseModel):
    scope_key: str
    scope_name: str
    scope_type: str
    platform: Optional[str] = None
    country: Optional[str] = None
    shop_ids: list[str] = []


class ScopeListResponse(BaseModel):
    items: list[ScopeItem]
    total: int


class ScopeBindingResponse(BaseModel):
    open_id: str
    scope_key: Optional[str] = None  # None = 显式全量 / 未设置
    scope: Optional[str] = None  # display_text，如 "TikTok Shop / 印尼 / 1 个店铺"
    is_set: bool


class DashboardLinkResponse(BaseModel):
    url: str  # 完整看板链接（public_base_url + /dashboard?t=<token>）
    expires_in: int  # token 有效期（秒）
    # 现成的飞书 markdown 片段：把可点击文字 + 有效期包好，agent 原样发即可。
    # 飞书卡片 lark_md 原生渲染 [文字](url) 成蓝色可点击链接，避免裸贴一长串带 token 的 URL。
    markdown: str


class SetScopeBindingRequest(BaseModel):
    open_id: str
    scope_key: Optional[str] = None  # None/"" = 切换为全量
    # channel / account_id 不在请求体暴露：飞书 open_id 是 per-app 唯一的，账号隔离已由
    # open_id 保证，account_id 维度冗余。写入与数据端点自动注入读取都走服务端默认
    # (feishu / ecom-app)，保证读写命中同一 binding 行——绝不让 agent 传 account_id
    # 制造读写不对齐（gtl 账号曾因此切范围静默失效）。多 app 真隔离留待 plan/09。


class TrendPoint(BaseModel):
    date: str
    gmv: float
    order_count: int
    units_sold: int


class TrendResponse(BaseModel):
    start_date: str
    end_date: str
    window_label: Optional[str] = None
    points: list[TrendPoint]
    scope: Optional[str] = None
    caliber: Optional[str] = None


class OverviewInventory(BaseModel):
    total_sku: int
    total_stock: int
    low_stock_count: int


class OverviewOrders(BaseModel):
    gmv: float
    order_count: int
    units_sold: int
    avg_order_value: float


class OverviewResponse(BaseModel):
    period: str
    scope: Optional[str] = None
    inventory: OverviewInventory
    orders: OverviewOrders


class LowStockItem(BaseModel):
    sku_id: str
    product_name: Optional[str] = None
    shop_id: Optional[str] = None
    available_stock: int
    daily_velocity: float  # 近期日均销量
    days_of_cover: float  # 可售天数 = 可用库存 ÷ 日均销速
    bucket: str  # stockout / critical / warning


class LowStockBuckets(BaseModel):
    stockout: int = 0
    critical: int = 0
    warning: int = 0
    total: int = 0


class LowStockResponse(BaseModel):
    items: list[LowStockItem]
    buckets: LowStockBuckets
    snapshot_at: Optional[str] = None  # 库存快照同步时间，印尼当地 UTC+7
    critical_days: int
    warning_days: int
    velocity_window_days: int
    scope: Optional[str] = None
    caliber: Optional[str] = None


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/inventory", response_model=InventoryResponse, operation_id="ops_inventory")
async def get_inventory(
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
    scope_id: Optional[str] = Query(None, description="业务范围 scope_key（命名店铺集合）"),
    shop_ids: Optional[str] = Query(None, description="店铺ID集合，逗号分隔"),
    low_stock_threshold: int = Query(10, description="低库存阈值"),
    open_id: Optional[str] = Query(None, description="飞书用户 open_id（ou_xxx，用于自动应用会话默认范围）"),
):
    """获取库存列表，同时返回低库存商品。

    口径：当前库存快照（无历史趋势）；available_stock < low_stock_threshold（默认 10）记为低库存；
    来源 TikTok /product/202309/inventory/search。
    """
    scope = _resolve_scope(
        scope_id=scope_id, platform=platform, country=country,
        shop_id=shop_id, shop_ids=shop_ids, open_id=open_id,
    )
    session = SessionLocal()
    try:
        query = session.query(Inventory)
        if scope.platform:
            query = query.filter(Inventory.platform == scope.platform)
        if scope.country:
            query = query.filter(Inventory.country == scope.country)
        if scope.shop_ids:
            query = query.filter(Inventory.shop_id.in_(scope.shop_ids))

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
            scope=scope.display_text,
        )
    finally:
        session.close()


LOW_STOCK_CALIBER = (
    "可售天数 = 可用库存 ÷ 日均销速；日均销速 = 近 N 天已付款销量 ÷ N（默认 N=7）。"
    "只统计仍有销量(velocity>0)的 SKU：库存为 0 且近期有销量记『断货』，可售<critical_days 记『告急』，"
    "<warning_days 记『预警』；无销量的死货/下架 SKU 不计入。库存按 SKU 跨店聚合（与销速口径对齐）。"
)


@router.get("/inventory/low-stock", response_model=LowStockResponse, operation_id="ops_low_stock")
async def get_low_stock(
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
    scope_id: Optional[str] = Query(None, description="业务范围 scope_key（命名店铺集合）"),
    shop_ids: Optional[str] = Query(None, description="店铺ID集合，逗号分隔"),
    critical_days: Optional[int] = Query(None, description="告急阈值：可售天数低于此值记『告急』（默认 3）"),
    warning_days: Optional[int] = Query(None, description="预警阈值：可售天数低于此值记『预警』（默认 7）"),
    open_id: Optional[str] = Query(None, description="飞书用户 open_id（ou_xxx，用于自动应用会话默认范围）"),
):
    """低库存 / 断货风险（按可售天数）。只列仍卖得动却快断货的 SKU，断货排最前。

    口径随响应 caliber 字段返回。与 ops_inventory 的静态阈值（库存<10）不同，本端点用
    可售天数 = 可用库存 ÷ 日均销速，能识别『库存看着不少但卖得快、即将断货』的爆款；
    无销量的死货不打扰。利润/ROI 本期未上线，不在此端点。
    """
    scope = _resolve_scope(
        scope_id=scope_id, platform=platform, country=country,
        shop_id=shop_id, shop_ids=shop_ids, open_id=open_id,
    )
    risk = get_stock_risk(
        platform=scope.platform,
        country=scope.country,
        shop_ids=scope.shop_ids or None,
        critical_days=critical_days,
        warning_days=warning_days,
    )
    return LowStockResponse(
        items=[LowStockItem(**i) for i in risk["items"]],
        buckets=LowStockBuckets(**risk["buckets"]),
        snapshot_at=risk["snapshot_at"],
        critical_days=risk["critical_days"],
        warning_days=risk["warning_days"],
        velocity_window_days=risk["velocity_window_days"],
        scope=scope.display_text,
        caliber=LOW_STOCK_CALIBER,
    )


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


@router.get("/overview", response_model=OverviewResponse, operation_id="ops_overview")
async def get_overview(
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
    scope_id: Optional[str] = Query(None, description="业务范围 scope_key（命名店铺集合）"),
    shop_ids: Optional[str] = Query(None, description="店铺ID集合，逗号分隔"),
    open_id: Optional[str] = Query(None, description="飞书用户 open_id（ou_xxx，用于自动应用会话默认范围）"),
):
    """经营概览：库存快照 + 近 7 天订单概览（不含利润/告警，本期未上线）。

    订单段为已付款订单口径（同 ops_orders_summary）；库存段为当前快照（低库存阈值 10）。
    """
    scope = _resolve_scope(
        scope_id=scope_id, platform=platform, country=country,
        shop_id=shop_id, shop_ids=shop_ids, open_id=open_id,
    )
    today = business_today()
    week_ago = today - timedelta(days=7)

    # 库存
    session = SessionLocal()
    try:
        inv_query = session.query(Inventory)
        if scope.platform:
            inv_query = inv_query.filter(Inventory.platform == scope.platform)
        if scope.country:
            inv_query = inv_query.filter(Inventory.country == scope.country)
        if scope.shop_ids:
            inv_query = inv_query.filter(Inventory.shop_id.in_(scope.shop_ids))
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
        platform=scope.platform,
        country=scope.country,
        shop_ids=scope.shop_ids,
    )

    return {
        "period": f"{week_ago.isoformat()} ~ {today.isoformat()}",
        "scope": scope.display_text,
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


@router.get("/orders/summary", response_model=OrderSummary, operation_id="ops_orders_summary")
async def get_orders_summary(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    period: Optional[str] = Query(None, description="相对时间窗口（按印尼时区、周一起算）：today/yesterday/this_week/last_week/last_7d/last_30d/this_month。相对时间优先用本参数，不要自己算日期；与 start_date/end_date 二选一，显式日期优先。"),
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
    scope_id: Optional[str] = Query(None, description="业务范围 scope_key（命名店铺集合）"),
    shop_ids: Optional[str] = Query(None, description="店铺ID集合，逗号分隔"),
    open_id: Optional[str] = Query(None, description="飞书用户 open_id（ou_xxx，用于自动应用会话默认范围）"),
):
    """已付款订单 GMV/订单量/销量/客单价汇总，默认最近7天（按 paid_time 归日，印尼当地时间 UTC+7）。

    相对时间（今天/本周/近7天…）传 `period` 参数，服务端按印尼时区+周一起算，**不要自己算日期**。
    口径（随响应 caliber 字段返回）：已付款订单（paid_time 非空、排除未付款/已取消）；
    GMV=订单 total_amount（买家实付，非平台结算）；销量=line_item 条数；客单价=GMV/订单数。
    """
    scope = _resolve_scope(
        scope_id=scope_id, platform=platform, country=country,
        shop_id=shop_id, shop_ids=shop_ids, open_id=open_id,
    )
    sd, ed = _resolve_window(start_date, end_date, period, default_back_days=7)

    data = get_gmv_summary(
        start_date=sd,
        end_date=ed,
        platform=scope.platform,
        country=scope.country,
        shop_ids=scope.shop_ids,
    )
    return OrderSummary(
        **data,
        window_label=describe_window(sd, ed),
        scope=scope.display_text,
        caliber=ORDERS_CALIBER,
    )


@router.get("/orders/top-skus", response_model=TopSkuResponse, operation_id="ops_top_skus")
async def get_orders_top_skus(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    period: Optional[str] = Query(None, description="相对时间窗口（按印尼时区、周一起算）：today/yesterday/this_week/last_week/last_7d/last_30d/this_month。相对时间优先用本参数，不要自己算日期；与 start_date/end_date 二选一，显式日期优先。"),
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
    scope_id: Optional[str] = Query(None, description="业务范围 scope_key（命名店铺集合）"),
    shop_ids: Optional[str] = Query(None, description="店铺ID集合，逗号分隔"),
    limit: int = Query(10, description="返回数量"),
    open_id: Optional[str] = Query(None, description="飞书用户 open_id（ou_xxx，用于自动应用会话默认范围）"),
):
    """已付款订单内按销量排序的单品榜，默认最近7天。

    相对时间（今天/本周/近7天…）传 `period` 参数，服务端按印尼时区+周一起算，**不要自己算日期**。
    口径（随响应 caliber 字段返回）：已付款订单口径；单品 GMV=该 SKU 各 line_item 的
    sale_price 之和（商品行售价，不含运费）；排序按销量（line_item 条数）降序。
    """
    scope = _resolve_scope(
        scope_id=scope_id, platform=platform, country=country,
        shop_id=shop_id, shop_ids=shop_ids, open_id=open_id,
    )
    sd, ed = _resolve_window(start_date, end_date, period, default_back_days=7)

    items = get_top_skus(
        start_date=sd,
        end_date=ed,
        platform=scope.platform,
        country=scope.country,
        shop_ids=scope.shop_ids,
        limit=limit,
    )
    return TopSkuResponse(
        items=[TopSkuItem(**i) for i in items],
        total=len(items),
        start_date=sd.isoformat(),
        end_date=ed.isoformat(),
        window_label=describe_window(sd, ed),
        scope=scope.display_text,
        caliber=TOP_SKUS_CALIBER,
    )


@router.get("/orders/trend", response_model=TrendResponse, operation_id="ops_orders_trend")
async def get_orders_trend(
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    period: Optional[str] = Query(None, description="相对时间窗口（按印尼时区、周一起算）：today/yesterday/this_week/last_week/last_7d/last_30d/this_month。相对时间优先用本参数，不要自己算日期；与 start_date/end_date 二选一，显式日期优先。"),
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID（店铺 GMV 趋势按此过滤）"),
    scope_id: Optional[str] = Query(None, description="业务范围 scope_key（命名店铺集合）"),
    shop_ids: Optional[str] = Query(None, description="店铺ID集合，逗号分隔"),
    open_id: Optional[str] = Query(None, description="飞书用户 open_id（ou_xxx，用于自动应用会话默认范围）"),
):
    """已付款订单按天的 GMV/单量/销量趋势，默认近 7 天（窗口内无单的日期补 0）。

    相对时间（近3天/近7天/本周/本月…）传 `period` 参数，服务端按印尼时区+周一起算，**不要自己算日期**；
    店铺 GMV 趋势传 shop_id 或 scope_id/shop_ids。口径与 ops_orders_summary 一致，随响应 caliber 字段返回。
    """
    scope = _resolve_scope(
        scope_id=scope_id, platform=platform, country=country,
        shop_id=shop_id, shop_ids=shop_ids, open_id=open_id,
    )
    sd, ed = _resolve_window(start_date, end_date, period, default_back_days=6)

    points = get_gmv_trend(
        start_date=sd,
        end_date=ed,
        platform=scope.platform,
        country=scope.country,
        shop_ids=scope.shop_ids,
    )
    return TrendResponse(
        start_date=sd.isoformat(),
        end_date=ed.isoformat(),
        window_label=describe_window(sd, ed),
        points=[TrendPoint(**p) for p in points],
        scope=scope.display_text,
        caliber=ORDERS_CALIBER,
    )


@router.get(
    "/fulfillments/pending",
    response_model=PendingFulfillmentsResponse,
    operation_id="ops_fulfillments_pending",
)
async def get_fulfillments_pending(
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
    scope_id: Optional[str] = Query(None, description="业务范围 scope_key（命名店铺集合）"),
    shop_ids: Optional[str] = Query(None, description="店铺ID集合，逗号分隔"),
    warning_hours: Optional[int] = Query(None, description="临界预警阈值（小时）：距发货截止不足此值记为临界，默认 24"),
    limit: int = Query(200, description="返回明细数量上限（计数与分店汇总不受此限）"),
    open_id: Optional[str] = Query(None, description="飞书用户 open_id（ou_xxx，用于自动应用会话默认范围）"),
):
    """待发货订单列表 + 超时/临界预警分桶 + 分店汇总（当前快照，无时间窗口）。

    用于"现在有几单待发货 / 几单快超时 / 已超时几单 / 哪个店该发货 / 今天该发哪些单"。
    口径（随响应 caliber 字段返回）：待发货快照（order_status=AWAITING_SHIPMENT）；
    超时=已过平台发货截止时间(tts_sla_time)，临界=距截止不足 warning_hours（默认 24）小时；
    所有时间为印尼当地时间 UTC+7，数据新鲜度见 snapshot_at。**这是当前快照，不接受相对时间参数**。
    """
    scope = _resolve_scope(
        scope_id=scope_id, platform=platform, country=country,
        shop_id=shop_id, shop_ids=shop_ids, open_id=open_id,
    )
    data = get_pending_fulfillments(
        platform=scope.platform,
        country=scope.country,
        shop_ids=scope.shop_ids,
        warning_hours=warning_hours,
        limit=limit,
    )
    return PendingFulfillmentsResponse(
        **data,
        scope=scope.display_text,
        caliber=FULFILLMENTS_CALIBER,
    )


@router.get("/products", response_model=ProductResponse, operation_id="ops_products")
async def get_products(
    platform: Optional[str] = Query(None, description="平台标识，如 tiktok_shop / shopee"),
    country: Optional[str] = Query(None, description="国家/地区，如 ID / GLOBAL"),
    shop_id: Optional[str] = Query(None, description="店铺ID"),
    scope_id: Optional[str] = Query(None, description="业务范围 scope_key（命名店铺集合）"),
    shop_ids: Optional[str] = Query(None, description="店铺ID集合，逗号分隔"),
    status: Optional[str] = Query(None, description="商品状态，如 ACTIVATE / SELLER_DEACTIVATED / DRAFT"),
    limit: int = Query(100, description="返回数量上限"),
    open_id: Optional[str] = Query(None, description="飞书用户 open_id（ou_xxx，用于自动应用会话默认范围）"),
):
    """商品目录列表，支持平台/国家/店铺/状态过滤，用于商品目录、上下架与滞销分析。

    口径：商品主数据来自 TikTok /product/202309/products/search（库存同步顺手入库）；
    跨境店 min_price/currency 常为 null 属正常（products/search 不一定返回价格），非数据缺口。
    """
    scope = _resolve_scope(
        scope_id=scope_id, platform=platform, country=country,
        shop_id=shop_id, shop_ids=shop_ids, open_id=open_id,
    )
    session = SessionLocal()
    try:
        query = session.query(Product)
        if scope.platform:
            query = query.filter(Product.platform == scope.platform)
        if scope.country:
            query = query.filter(Product.country == scope.country)
        if scope.shop_ids:
            query = query.filter(Product.shop_id.in_(scope.shop_ids))
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
        return ProductResponse(items=items, total=len(items), scope=scope.display_text)
    finally:
        session.close()


@router.get("/scopes", response_model=ScopeListResponse, operation_id="ops_scopes")
async def get_scopes():
    """列出所有启用的业务范围（scope）。用于 agent 在用户问"有哪些范围"时回答。"""
    scopes = list_scopes()
    return ScopeListResponse(
        items=[ScopeItem(**s) for s in scopes],
        total=len(scopes),
    )


@router.post(
    "/scope/binding", response_model=ScopeBindingResponse, operation_id="ops_set_scope_binding"
)
async def set_scope_binding(body: SetScopeBindingRequest):
    """写该用户的会话默认查询范围（菜单切换默认范围时调用）。

    `scope_key` 传命名 scope（如 `tts-id-all`）切到该范围；传空/省略切为全量。
    未知或已停用的 scope_key → 400。写入后返回该范围展示文案，用于"已切换到 X"确认话术。
    """
    try:
        # channel/account_id 走服务端默认（与 _resolve_scope 的自动注入读取一致）。
        data = set_binding(body.open_id, body.scope_key)
    except ScopeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ScopeBindingResponse(open_id=body.open_id, **data)


@router.get(
    "/dashboard/link", response_model=DashboardLinkResponse, operation_id="ops_dashboard_link"
)
async def get_dashboard_link(
    open_id: str = Query(..., description="飞书用户 open_id（ou_xxx），看板范围按此账号锁定"),
):
    """签发一条带签名 token 的看板链接，用户点开即看自己范围内的运营看板。

    用户问「看板 / 数据大盘 / 趋势图」时调用，**把返回的 `markdown` 字段原样发给用户**
    （已是飞书可点击链接格式，别贴裸 `url`——那是一长串带 token 的丑字符串）。看板范围由
    open_id 的会话默认范围（binding）锁定，token 短时效（默认 30 分钟）后失效，需重新获取。
    """
    base = settings.dashboard.public_base_url
    if not base:
        raise HTTPException(status_code=503, detail="DASHBOARD__PUBLIC_BASE_URL 未配置")
    ttl = settings.dashboard.token_ttl_seconds
    try:
        token = make_token(open_id, ttl=ttl)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    url = base.rstrip("/") + "/dashboard?t=" + token
    mins = max(1, ttl // 60)
    markdown = f"📊 [打开运营看板]({url})\n（链接 {mins} 分钟内有效，过期重新获取即可）"
    # 审计日志：弱模型理论上可能把 A 的链接签给 B（软隔离根本局限），靠短时效 + 此日志缓解。
    logger.info("dashboard link issued: open_id=%s ttl=%ss", open_id, ttl)
    return DashboardLinkResponse(url=url, expires_in=ttl, markdown=markdown)

