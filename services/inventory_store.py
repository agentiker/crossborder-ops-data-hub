"""Inventory persistence helpers with idempotent upsert semantics."""
from __future__ import annotations

from models.base_models import Inventory
from services.scoping import build_inventory_key


def upsert_inventory_items(
    session,
    items,
    *,
    platform: str = "tiktok_shop",
    country: str = "GLOBAL",
    shop_id: str | None = None,
    seller_id: str | None = None,
    account_id: str | None = None,
) -> int:
    """Upsert inventory rows by sku_id and warehouse_id."""
    for item in items:
        idempotency_key = build_inventory_key(
            platform=platform,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            warehouse_id=item.warehouse_id,
            sku_id=item.sku_id,
        )
        existing = (
            session.query(Inventory)
            .filter_by(idempotency_key=idempotency_key)
            .first()
        )
        if existing:
            existing.product_id = item.product_id
            existing.product_name = item.product_name
            existing.sku_name = item.sku_name
            existing.available_stock = item.available_stock
            existing.reserved_stock = item.reserved_stock
        else:
            session.add(
                Inventory(
                    platform=platform,
                    country=country,
                    shop_id=shop_id,
                    seller_id=seller_id,
                    account_id=account_id,
                    idempotency_key=idempotency_key,
                    sku_id=item.sku_id,
                    product_id=item.product_id,
                    product_name=item.product_name,
                    sku_name=item.sku_name,
                    available_stock=item.available_stock,
                    reserved_stock=item.reserved_stock,
                    warehouse_id=item.warehouse_id,
                )
            )
    session.flush()
    return len(items)
