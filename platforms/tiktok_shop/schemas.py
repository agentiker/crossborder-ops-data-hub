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
