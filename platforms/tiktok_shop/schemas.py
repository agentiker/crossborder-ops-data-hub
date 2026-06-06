"""
Pydantic数据模型
用于API响应数据的校验和清洗
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class InventoryItem(BaseModel):
    """库存数据模型（一行 = 一个 SKU 在一个仓库的库存）。

    对齐 POST /product/202309/inventory/search 响应，由 flatten_inventory 展平而来。
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


def flatten_inventory(
    inventory_list: list[dict],
    product_titles: Optional[dict[str, str]] = None,
) -> list[dict]:
    """把 inventory/search 的嵌套响应拍平成 InventoryItem 行。

    inventory[].skus[].warehouse_inventory[] → 每个 SKU×仓库一行。
    若某 SKU 无 warehouse_inventory（理论少见），回退为一行跨仓汇总
    （warehouse_id=None，用 total_available/total_committed）。

    Args:
        inventory_list: 响应 data.inventory[]
        product_titles: {product_id: title}，用于回填 product_name（来自 products/search）
    """
    titles = product_titles or {}
    rows: list[dict] = []
    for inv in inventory_list:
        product_id = inv.get("product_id")
        product_name = titles.get(product_id)
        for sku in inv.get("skus", []):
            base = {
                "product_id": product_id,
                "product_name": product_name,
                "sku_id": sku.get("id"),
                "sku_name": sku.get("seller_sku"),
            }
            warehouses = sku.get("warehouse_inventory") or []
            if warehouses:
                for wh in warehouses:
                    rows.append({
                        **base,
                        "warehouse_id": wh.get("warehouse_id"),
                        "available_stock": wh.get("available_quantity", 0) or 0,
                        "reserved_stock": wh.get("committed_quantity", 0) or 0,
                    })
            else:
                rows.append({
                    **base,
                    "warehouse_id": None,
                    "available_stock": sku.get("total_available_quantity", 0) or 0,
                    "reserved_stock": sku.get("total_committed_quantity", 0) or 0,
                })
    return rows


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
