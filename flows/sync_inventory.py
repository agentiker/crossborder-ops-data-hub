"""Prefect inventory sync flow."""

from datetime import datetime, timezone
from typing import Any, Optional

from prefect import flow, task
from flows.network import log_egress_ip
from platforms.tiktok_shop.client import TikTokShopClient
from platforms.tiktok_shop.client import PLATFORM as TIKTOK_PLATFORM
from platforms.tiktok_shop.schemas import InventoryItem
from models.base_models import Inventory
from core.db import SessionLocal
from services.scoping import build_inventory_key
from services.sync_state import record_raw_response, upsert_cursor


@task(name="fetch-tiktok-inventory", retries=3, retry_delay_seconds=60)
def fetch_inventory(
    *,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """从TikTok API获取库存数据"""
    client = TikTokShopClient(
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    pages = []
    for page in client.iter_inventory(warehouse_id=warehouse_id):
        pages.append(page)
    return pages


@task(name="validate-inventory")
def validate_inventory(raw_items: list[dict[str, Any]]) -> list[InventoryItem]:
    """Pydantic校验清洗"""
    valid_items = []
    for raw in raw_items:
        try:
            item = InventoryItem.model_validate(raw)
            valid_items.append(item)
        except Exception as e:
            print(f"数据校验失败: {e}")
    return valid_items


def flatten_inventory_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract inventory items from paginated API payloads."""
    items = []
    for page in pages:
        items.extend(page.get("inventory_list", []))
    return items


@task(name="flatten-inventory-pages")
def flatten_inventory_pages_task(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return flatten_inventory_pages(pages)


def upsert_inventory_items(
    session,
    items: list[InventoryItem],
    *,
    platform: str = TIKTOK_PLATFORM,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    raw_response_id: Optional[int] = None,
) -> int:
    """Write inventory snapshots idempotently."""
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
        existing = session.query(Inventory).filter_by(
            idempotency_key=idempotency_key
        ).first()

        if existing:
            existing.product_id = item.product_id
            existing.product_name = item.product_name
            existing.sku_name = item.sku_name
            existing.available_stock = item.available_stock
            existing.reserved_stock = item.reserved_stock
            existing.source_updated_at = item.updated_at
            existing.raw_response_id = raw_response_id
        else:
            session.add(Inventory(
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
                source_updated_at=item.updated_at,
                raw_response_id=raw_response_id,
            ))
    session.flush()
    return len(items)


@task(name="save-inventory-to-db", retries=2, retry_delay_seconds=30)
def save_to_db(
    pages: list[dict[str, Any]],
    items: list[InventoryItem],
    *,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> int:
    """写入MySQL（Upsert）并记录 raw payload 与 cursor."""
    session = SessionLocal()
    try:
        raw_record = record_raw_response(
            session,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            resource="inventory",
            method="GET",
            path="/api/inventory/get",
            response_payload={"pages": pages},
            http_status=200,
            business_code="0",
        )
        count = upsert_inventory_items(
            session,
            items,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            raw_response_id=raw_record.id,
        )
        upsert_cursor(
            session,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            resource="inventory",
            window_end=datetime.now(timezone.utc),
            extra={"item_count": count, "page_count": len(pages)},
        )
        session.commit()
        return count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@flow(name="tiktok-inventory-sync", log_prints=True)
def sync_inventory_flow(
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
):
    """库存同步主流程（带任务依赖）"""
    log_egress_ip()
    pages = fetch_inventory(
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        warehouse_id=warehouse_id,
    )
    raw_items = flatten_inventory_pages_task(pages)
    valid_data = validate_inventory(raw_items)
    count = save_to_db(
        pages,
        valid_data,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    print(f"同步完成: {count}条记录")
    return count

if __name__ == "__main__":
    sync_inventory_flow()
