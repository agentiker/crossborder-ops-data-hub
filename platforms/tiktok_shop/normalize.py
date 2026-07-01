"""TikTok Shop 原始 API 数据 → 平台中立领域模型（core.domain）。

平台所有数据怪癖在此唯一收敛：金额字符串 → Decimal、Unix 秒 → naive UTC datetime、
库存嵌套展平、商品最低价清洗、line item 的 currency fallback、逐条校验容错。对外只
暴露 to_domain_orders / to_domain_inventory / to_domain_products，输入原始 dict、输出
已清洗的 DTO。schemas.py 的 Pydantic 模型在此作 wire 校验中间态被使用，不外泄。

services 持久层只认 core.domain 的 DTO，不应 import 本模块或 schemas。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from core.domain import (
    DomainInventoryItem,
    DomainOrder,
    DomainOrderLineItem,
    DomainProduct,
)
from platforms.tiktok_shop.schemas import InventoryItem, OrderSchema


# ── 通用清洗 helper（平台怪癖：金额是字符串、时间是 Unix 秒）────────────────────

def _to_decimal(value) -> Decimal:
    """TTS 字符串金额 → Decimal，容错 None/空/garbage → 0。"""
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _epoch_to_dt(value) -> Optional[datetime]:
    """Unix 秒 → naive UTC datetime（0/None/空 → None）。

    必须产 naive UTC：core.timezone.to_business_day 依赖 paid_time 为 naive UTC，
    带 tzinfo 会在归日运算时报错。
    """
    if value in (None, "", 0):
        return None
    try:
        return datetime.utcfromtimestamp(int(value))
    except (TypeError, ValueError, OSError):
        return None


# ── 订单 ──────────────────────────────────────────────────────────────────

def flatten_order_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """订单 search 分页响应 → 扁平的原始订单 dict 列表。"""
    orders: list[dict[str, Any]] = []
    for page in pages:
        orders.extend(page.get("orders", []))
    return orders


def _order_to_domain(order: OrderSchema) -> DomainOrder:
    payment = order.payment
    currency = payment.currency if payment else None
    lines = tuple(
        DomainOrderLineItem(
            line_item_id=line.id,
            product_id=line.product_id,
            product_name=line.product_name,
            sku_id=line.sku_id,
            sku_name=line.sku_name,
            seller_sku=line.seller_sku,
            sale_price=_to_decimal(line.sale_price),
            original_price=_to_decimal(line.original_price),
            currency=line.currency or currency,  # line 无币种则回退到订单 payment 币种
            display_status=line.display_status,
        )
        for line in order.line_items
    )
    return DomainOrder(
        order_id=order.id,
        order_status=order.status,
        currency=currency,
        total_amount=_to_decimal(payment.total_amount if payment else None),
        sub_total=_to_decimal(payment.sub_total if payment else None),
        is_cod=bool(order.is_cod),
        buyer_message=order.buyer_message,
        warehouse_id=order.warehouse_id,
        create_time=_epoch_to_dt(order.create_time),
        paid_time=_epoch_to_dt(order.paid_time),
        update_time=_epoch_to_dt(order.update_time),
        tts_sla_time=_epoch_to_dt(order.tts_sla_time),
        rts_sla_time=_epoch_to_dt(order.rts_sla_time),
        shipping_due_time=_epoch_to_dt(order.shipping_due_time),
        collection_due_time=_epoch_to_dt(order.collection_due_time),
        delivery_option_name=order.delivery_option_name,
        line_items=lines,
    )


def to_domain_orders(pages: list[dict[str, Any]]) -> list[DomainOrder]:
    """订单分页 dict → list[DomainOrder]。逐条 Pydantic 校验，坏数据打印跳过不中断整批。"""
    orders: list[DomainOrder] = []
    for raw in flatten_order_pages(pages):
        try:
            schema = OrderSchema.model_validate(raw)
        except Exception as e:  # noqa: BLE001
            print(f"订单校验失败: {e}")
            continue
        orders.append(_order_to_domain(schema))
    return orders


# ── 库存 ──────────────────────────────────────────────────────────────────

def flatten_inventory(
    inventory_list: list[dict],
    product_titles: Optional[dict[str, str]] = None,
) -> list[dict]:
    """把 inventory/search 的嵌套响应拍平成行。

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


def to_domain_inventory(
    inventory: list[dict],
    product_titles: Optional[dict[str, str]] = None,
) -> list[DomainInventoryItem]:
    """库存嵌套响应 → list[DomainInventoryItem]。逐条校验，坏数据打印跳过不中断整批。"""
    items: list[DomainInventoryItem] = []
    for row in flatten_inventory(inventory, product_titles):
        try:
            schema = InventoryItem.model_validate(row)
        except Exception as e:  # noqa: BLE001
            print(f"库存校验失败: {e}")
            continue
        items.append(
            DomainInventoryItem(
                sku_id=schema.sku_id,
                product_id=schema.product_id,
                product_name=schema.product_name,
                sku_name=schema.sku_name,
                warehouse_id=schema.warehouse_id,
                available_stock=schema.available_stock,
                reserved_stock=schema.reserved_stock,
                source_updated_at=schema.updated_at,
            )
        )
    return items


# ── 商品 ──────────────────────────────────────────────────────────────────

def _min_sku_price(skus: list[dict]) -> tuple[Optional[Decimal], Optional[str]]:
    """从 skus[].price 取最低售价及其币种（缺价跳过）。

    取价字段优先 sale_price（商品页含税展示价，运营口径），缺失时回退
    tax_exclusive_price（税前价）。products/search 对本类卖家只回 tax_exclusive_price，
    含税 sale_price 须由商品详情（client.get_product）的 skus 提供，故 to_domain_products
    优先喂详情 skus。

    比较用 float（与历史一致地选出"哪个 SKU 最便宜"），落库用选中那条的原始字符串转
    Decimal，避免 float 精度损失。
    """
    best_value: Optional[float] = None
    best_raw: Optional[str] = None
    best_currency: Optional[str] = None
    for sku in skus:
        price = sku.get("price") or {}
        raw = price.get("sale_price") or price.get("tax_exclusive_price")
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if best_value is None or value < best_value:
            best_value = value
            best_raw = raw
            best_currency = price.get("currency")
    min_price = _to_decimal(best_raw) if best_raw is not None else None
    return min_price, best_currency


def to_domain_products(
    products: list[dict],
    price_skus_by_id: Optional[dict[str, list[dict]]] = None,
    images_by_id: Optional[dict[str, str]] = None,
) -> list[DomainProduct]:
    """products/search 的 products[] → list[DomainProduct]（丢无 id 项，清洗最低价/时间）。

    price_skus_by_id：{product_id: 商品详情的 skus[]}，用于取含税 sale_price 算 min_price
    （products/search 不回 sale_price）。某商品缺映射时回退用 search 自带 skus 的税前价。
    images_by_id：{product_id: 主图缩略图 URL}（来自商品详情 main_images，看板爆款小图）。
    sku_count 始终以 search 的 skus 为准（枚举口径不变）。
    """
    price_skus_by_id = price_skus_by_id or {}
    images_by_id = images_by_id or {}
    items: list[DomainProduct] = []
    for p in products:
        product_id = p.get("id")
        if not product_id:
            continue
        skus = p.get("skus") or []
        price_skus = price_skus_by_id.get(product_id) or skus
        min_price, currency = _min_sku_price(price_skus)
        items.append(
            DomainProduct(
                product_id=product_id,
                title=p.get("title"),
                status=p.get("status"),
                sales_regions=p.get("sales_regions") or None,
                sku_count=len(skus),
                min_price=min_price,
                currency=currency,
                main_image_url=images_by_id.get(product_id),
                source_create_time=_epoch_to_dt(p.get("create_time")),
                source_update_time=_epoch_to_dt(p.get("update_time")),
            )
        )
    return items
