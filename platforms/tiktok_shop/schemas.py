"""
Pydantic数据模型
用于API响应数据的校验和清洗
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class InventoryItem(BaseModel):
    """库存数据模型"""
    sku_id: str
    product_id: str
    product_name: str
    sku_name: str
    available_stock: int = Field(ge=0)
    reserved_stock: int = Field(ge=0, default=0)
    warehouse_id: Optional[str] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ── 订单模型（对齐 TTS order/202309 响应 json tag）─────────────────────────────


class OrderPayment(BaseModel):
    """订单支付明细（取自 order.payment）。金额为字符串，转换在 store 层处理。"""
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
