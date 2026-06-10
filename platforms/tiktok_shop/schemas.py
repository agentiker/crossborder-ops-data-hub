"""
Pydantic 数据模型：校验/裁剪 TikTok Shop API 的原始负载。

这些模型是 wire 格式的忠实镜像（字符串金额、Unix 秒），只做校验与字段裁剪
（extra="ignore"），**不做类型转换**。原始 dict → 平台中立领域模型（core.domain）的
转换在 platforms/tiktok_shop/normalize.py 完成，本模块的模型在那里作为校验中间态被使用。
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class InventoryItem(BaseModel):
    """库存数据模型（一行 = 一个 SKU 在一个仓库的库存）。

    对齐 POST /product/202309/inventory/search 响应，由 normalize.flatten_inventory 展平而来。
    """
    sku_id: str
    product_id: str
    product_name: Optional[str] = None  # search 响应无商品名，由 products/search title 透传
    sku_name: Optional[str] = None      # 取 SKU 的 seller_sku
    available_stock: int = Field(ge=0)
    reserved_stock: int = Field(ge=0, default=0)
    warehouse_id: Optional[str] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── 订单模型（对齐 TTS order/202309 响应 json tag）─────────────────────────────


class OrderPayment(BaseModel):
    """订单支付明细（取自 order.payment）。金额为字符串，转换在 normalize 层处理。"""
    currency: Optional[str] = None
    total_amount: Optional[str] = None
    sub_total: Optional[str] = None
    tax: Optional[str] = None
    shipping_fee: Optional[str] = None

    class Config:
        extra = "ignore"


class OrderLineItemSchema(BaseModel):
    """订单行项（每条 = 售出一件）。"""
    id: str
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    sku_id: Optional[str] = None
    sku_name: Optional[str] = None
    seller_sku: Optional[str] = None
    sale_price: Optional[str] = None
    original_price: Optional[str] = None
    currency: Optional[str] = None
    display_status: Optional[str] = None

    class Config:
        extra = "ignore"


class OrderSchema(BaseModel):
    """订单主体（对齐 order/202309 list/detail 的 orders[] 结构）。"""
    id: str
    status: Optional[str] = None
    create_time: Optional[int] = None
    paid_time: Optional[int] = None
    update_time: Optional[int] = None
    is_cod: Optional[bool] = None
    buyer_message: Optional[str] = None
    warehouse_id: Optional[str] = None
    payment: Optional[OrderPayment] = None
    line_items: list[OrderLineItemSchema] = Field(default_factory=list)

    class Config:
        extra = "ignore"
