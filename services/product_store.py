"""Product master persistence helpers with idempotent upsert semantics."""
from __future__ import annotations

from models.base_models import Product
from services.scoping import build_product_key


def upsert_products(
    session,
    items,
    *,
    platform: str = "tiktok_shop",
    country: str = "GLOBAL",
    shop_id: str | None = None,
    seller_id: str | None = None,
    account_id: str | None = None,
    raw_response_id: int | None = None,
) -> int:
    """Upsert product rows by product_id within the account scope."""
    for item in items:
        idempotency_key = build_product_key(
            platform=platform,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            product_id=item.product_id,
        )
        existing = (
            session.query(Product)
            .filter_by(idempotency_key=idempotency_key)
            .first()
        )
        if existing:
            existing.title = item.title
            existing.status = item.status
            existing.sales_regions = item.sales_regions
            existing.sku_count = item.sku_count
            existing.min_price = item.min_price
            existing.currency = item.currency
            existing.source_create_time = item.source_create_time
            existing.source_update_time = item.source_update_time
            existing.raw_response_id = raw_response_id
        else:
            session.add(
                Product(
                    platform=platform,
                    country=country,
                    shop_id=shop_id,
                    seller_id=seller_id,
                    account_id=account_id,
                    idempotency_key=idempotency_key,
                    product_id=item.product_id,
                    title=item.title,
                    status=item.status,
                    sales_regions=item.sales_regions,
                    sku_count=item.sku_count,
                    min_price=item.min_price,
                    currency=item.currency,
                    source_create_time=item.source_create_time,
                    source_update_time=item.source_update_time,
                    raw_response_id=raw_response_id,
                )
            )
    session.flush()
    return len(items)
