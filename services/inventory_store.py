"""Inventory persistence helpers with idempotent upsert semantics."""
from __future__ import annotations

from core.domain import DomainInventoryItem
from models.base_models import Inventory
from services.product_store import scope_filters
from services.scoping import build_inventory_key


def upsert_inventory_items(
    session,
    items: list[DomainInventoryItem],
    *,
    platform: str = "tiktok_shop",
    country: str = "GLOBAL",
    shop_id: str | None = None,
    seller_id: str | None = None,
    account_id: str | None = None,
    raw_response_id: int | None = None,
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
            existing.source_updated_at = item.source_updated_at
            existing.raw_response_id = raw_response_id
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
                    source_updated_at=item.source_updated_at,
                    raw_response_id=raw_response_id,
                )
            )
    session.flush()
    return len(items)


def prune_inventory_not_in(
    session,
    items: list[DomainInventoryItem],
    *,
    platform: str = "tiktok_shop",
    country: str = "GLOBAL",
    shop_id: str | None = None,
    seller_id: str | None = None,
    account_id: str | None = None,
) -> int:
    """删除本店 inventory 中不在本次返回 SKU 集合的行（清退非在售商品 SKU / 已删变体）。

    基准用本次 items 重建的 idempotency_key 全集——既清退草稿/下架商品的 SKU，也清退活跃
    商品里被移除的旧变体。护栏：items 为空时不删任何行（防 API 异常清空全店库存）。
    返回删除行数。
    """
    if not items:
        return 0
    active_keys = {
        build_inventory_key(
            platform=platform,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            warehouse_id=item.warehouse_id,
            sku_id=item.sku_id,
        )
        for item in items
    }
    if not active_keys:
        return 0
    filters = scope_filters(
        Inventory,
        platform=platform,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    return (
        session.query(Inventory)
        .filter(*filters, Inventory.idempotency_key.notin_(active_keys))
        .delete(synchronize_session=False)
    )

