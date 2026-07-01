"""平台中立的领域模型（已清洗、不可变契约）。

平台特定的数据怪癖（金额是字符串、时间是 Unix 秒、库存嵌套、字段命名）一律在
`platforms/<platform>/normalize.py` 收敛掉；`services` 持久层只认本模块的 DTO 与
`models/base_models` ORM，**不 import 任何 platforms 下的 schema/normalize**。

字段命名对齐 `models/base_models`，让 store 成为 DTO→ORM 的纯映射。中立类型约定：
金额一律 `Decimal`、时间一律 naive UTC `datetime`、嵌套一律已展平。frozen 表达
"已清洗、不可变"，store 只读不改。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class DomainOrderLineItem:
    """订单行项（每条 = 售出一件）。`currency` 已应用 order.payment 的 fallback。"""
    line_item_id: str
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    sku_id: Optional[str] = None
    sku_name: Optional[str] = None
    seller_sku: Optional[str] = None
    sale_price: Decimal = Decimal("0")
    original_price: Decimal = Decimal("0")
    currency: Optional[str] = None
    display_status: Optional[str] = None


@dataclass(frozen=True)
class DomainOrder:
    """订单主体。`currency`/`total_amount` 已从平台 payment 拆出到顶层。"""
    order_id: str
    order_status: Optional[str] = None
    currency: Optional[str] = None
    total_amount: Decimal = Decimal("0")
    sub_total: Decimal = Decimal("0")  # 商品小计（payment.sub_total，不含运费/税/优惠）——展示 GMV 对齐后台口径
    is_cod: bool = False
    buyer_message: Optional[str] = None
    warehouse_id: Optional[str] = None
    create_time: Optional[datetime] = None
    paid_time: Optional[datetime] = None
    update_time: Optional[datetime] = None
    # 发货时效（SLA）相关，仅待发货快照流程消费；订单 GMV 流程不依赖（全为可选、向后兼容）。
    # 语义按 TTS Go SDK 注释：tts=最晚揽收、rts=最晚发货、*_due_time=超时平台自动取消线。
    tts_sla_time: Optional[datetime] = None
    rts_sla_time: Optional[datetime] = None
    shipping_due_time: Optional[datetime] = None
    collection_due_time: Optional[datetime] = None
    delivery_option_name: Optional[str] = None  # 配送方式，如 Economy / Standard / Express
    line_items: tuple[DomainOrderLineItem, ...] = ()


@dataclass(frozen=True)
class DomainInventoryItem:
    """库存项（一行 = 一个 SKU 在一个仓库的库存）。"""
    sku_id: str
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    sku_name: Optional[str] = None
    warehouse_id: Optional[str] = None
    available_stock: int = 0
    reserved_stock: int = 0
    source_updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class DomainSkuVariant:
    """SKU 变体（一行 = 一个 SKU 的颜色/尺码等属性）。attributes 为全部销售属性原样。"""
    sku_id: str
    product_id: Optional[str] = None
    seller_sku: Optional[str] = None
    product_name: Optional[str] = None
    color: Optional[str] = None
    size: Optional[str] = None
    attributes: Optional[list[dict]] = None


@dataclass(frozen=True)
class DomainProduct:
    """商品主数据（一行 = 一个商品）。`min_price` 为该商品 SKU 最低售价。"""
    product_id: str
    title: Optional[str] = None
    status: Optional[str] = None
    sales_regions: Optional[list[str]] = None
    sku_count: int = 0
    min_price: Optional[Decimal] = None
    currency: Optional[str] = None
    main_image_url: Optional[str] = None  # 主图缩略图 URL（看板爆款小图）
    source_create_time: Optional[datetime] = None
    source_update_time: Optional[datetime] = None
