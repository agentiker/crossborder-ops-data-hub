"""Order persistence helpers with idempotent upsert semantics."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from models.base_models import OrderHeader, OrderLineItem
from platforms.tiktok_shop.schemas import OrderSchema
from services.scoping import build_order_key, build_order_line_key


def _to_decimal(value) -> Decimal:
    """Convert a TTS string amount to Decimal, tolerating None/empty/garbage."""
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _to_datetime(unix_seconds: Optional[int]) -> Optional[datetime]:
    """Convert a Unix second timestamp to a naive UTC datetime."""
    if not unix_seconds:
        return None
    return datetime.fromtimestamp(int(unix_seconds), tz=timezone.utc).replace(tzinfo=None)


def upsert_orders(
    session,
    orders: list[OrderSchema],
    *,
    platform: str = "tiktok_shop",
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    raw_response_id: Optional[int] = None,
) -> tuple[int, int]:
    """Upsert order headers and their line items idempotently.

    Returns (order_count, line_item_count).
    """
    line_count = 0
    for order in orders:
        payment = order.payment
        header_key = build_order_key(
            platform=platform,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            order_id=order.id,
        )
        currency = payment.currency if payment else None
        total_amount = _to_decimal(payment.total_amount if payment else None)
        create_dt = _to_datetime(order.create_time)
        paid_dt = _to_datetime(order.paid_time)
        update_dt = _to_datetime(order.update_time)

        existing = (
            session.query(OrderHeader)
            .filter_by(idempotency_key=header_key)
            .first()
        )
        if existing:
            existing.order_status = order.status
            existing.currency = currency
            existing.total_amount = total_amount
            existing.is_cod = bool(order.is_cod)
            existing.buyer_message = order.buyer_message
            existing.warehouse_id = order.warehouse_id
            existing.create_time = create_dt
            existing.paid_time = paid_dt
            existing.update_time = update_dt
            existing.source_updated_at = update_dt
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
                    order_id=order.id,
                    order_status=order.status,
                    currency=currency,
                    total_amount=total_amount,
                    is_cod=bool(order.is_cod),
                    buyer_message=order.buyer_message,
                    warehouse_id=order.warehouse_id,
                    create_time=create_dt,
                    paid_time=paid_dt,
                    update_time=update_dt,
                    source_updated_at=update_dt,
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
                line_item_id=line.id,
            )
            existing_line = (
                session.query(OrderLineItem)
                .filter_by(idempotency_key=line_key)
                .first()
            )
            if existing_line:
                existing_line.order_id = order.id
                existing_line.sku_id = line.sku_id
                existing_line.seller_sku = line.seller_sku
                existing_line.product_id = line.product_id
                existing_line.product_name = line.product_name
                existing_line.sku_name = line.sku_name
                existing_line.sale_price = _to_decimal(line.sale_price)
                existing_line.original_price = _to_decimal(line.original_price)
                existing_line.currency = line.currency or currency
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
                        line_item_id=line.id,
                        order_id=order.id,
                        sku_id=line.sku_id,
                        seller_sku=line.seller_sku,
                        product_id=line.product_id,
                        product_name=line.product_name,
                        sku_name=line.sku_name,
                        sale_price=_to_decimal(line.sale_price),
                        original_price=_to_decimal(line.original_price),
                        currency=line.currency or currency,
                        display_status=line.display_status,
                        raw_response_id=raw_response_id,
                    )
                )

    session.flush()
    return len(orders), line_count
