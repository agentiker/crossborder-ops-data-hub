"""Order persistence helpers with idempotent upsert semantics."""
from __future__ import annotations

from typing import Optional

from core.domain import DomainOrder
from models.base_models import OrderHeader, OrderLineItem
from services.scoping import build_order_key, build_order_line_key


def upsert_orders(
    session,
    orders: list[DomainOrder],
    *,
    platform: str = "tiktok_shop",
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    raw_response_id: Optional[int] = None,
) -> tuple[int, int]:
    """Upsert order headers and their line items idempotently.

    入参是平台中立的 `DomainOrder`：金额已是 Decimal、时间已是 naive UTC datetime、
    line item 的 currency fallback 已在 platforms/<x>/normalize 完成。本函数只做
    DTO→ORM 的纯映射与幂等 upsert，不认识任何平台格式。

    Returns (order_count, line_item_count).
    """
    line_count = 0
    for order in orders:
        header_key = build_order_key(
            platform=platform,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            order_id=order.order_id,
        )

        existing = (
            session.query(OrderHeader)
            .filter_by(idempotency_key=header_key)
            .first()
        )
        if existing:
            existing.order_status = order.order_status
            existing.currency = order.currency
            existing.total_amount = order.total_amount
            existing.is_cod = order.is_cod
            existing.buyer_message = order.buyer_message
            existing.warehouse_id = order.warehouse_id
            existing.create_time = order.create_time
            existing.paid_time = order.paid_time
            existing.update_time = order.update_time
            existing.source_updated_at = order.update_time
            existing.raw_response_id = raw_response_id
        else:
            session.add(
                OrderHeader(
                    platform=platform,
                    country=country,
                    shop_id=shop_id,
                    seller_id=seller_id,
                    account_id=account_id,
                    idempotency_key=header_key,
                    order_id=order.order_id,
                    order_status=order.order_status,
                    currency=order.currency,
                    total_amount=order.total_amount,
                    is_cod=order.is_cod,
                    buyer_message=order.buyer_message,
                    warehouse_id=order.warehouse_id,
                    create_time=order.create_time,
                    paid_time=order.paid_time,
                    update_time=order.update_time,
                    source_updated_at=order.update_time,
                    raw_response_id=raw_response_id,
                )
            )

        for line in order.line_items:
            line_count += 1
            line_key = build_order_line_key(
                platform=platform,
                country=country,
                shop_id=shop_id,
                seller_id=seller_id,
                account_id=account_id,
                line_item_id=line.line_item_id,
            )
            existing_line = (
                session.query(OrderLineItem)
                .filter_by(idempotency_key=line_key)
                .first()
            )
            if existing_line:
                existing_line.order_id = order.order_id
                existing_line.sku_id = line.sku_id
                existing_line.seller_sku = line.seller_sku
                existing_line.product_id = line.product_id
                existing_line.product_name = line.product_name
                existing_line.sku_name = line.sku_name
                existing_line.sale_price = line.sale_price
                existing_line.original_price = line.original_price
                existing_line.currency = line.currency
                existing_line.display_status = line.display_status
                existing_line.raw_response_id = raw_response_id
            else:
                session.add(
                    OrderLineItem(
                        platform=platform,
                        country=country,
                        shop_id=shop_id,
                        seller_id=seller_id,
                        account_id=account_id,
                        idempotency_key=line_key,
                        line_item_id=line.line_item_id,
                        order_id=order.order_id,
                        sku_id=line.sku_id,
                        seller_sku=line.seller_sku,
                        product_id=line.product_id,
                        product_name=line.product_name,
                        sku_name=line.sku_name,
                        sale_price=line.sale_price,
                        original_price=line.original_price,
                        currency=line.currency,
                        display_status=line.display_status,
                        raw_response_id=raw_response_id,
                    )
                )

    session.flush()
    return len(orders), line_count
