"""Prefect inventory sync flow.

两步流程：先 products/search 枚举全店商品（拿 product_id 与 title），再分批
inventory/search 查库存。inventory/search 必须按 product_id 查、本身无翻页，故依赖
products 枚举。响应嵌套 inventory[].skus[].warehouse_inventory[]，展平为每个
SKU×仓库一行后幂等 upsert。
"""

from datetime import datetime, timezone
from typing import Any, Optional

from prefect import flow, task

from core.db import SessionLocal
from flows.network import log_egress_ip
from platforms.tiktok_shop.client import PLATFORM as TIKTOK_PLATFORM
from platforms.tiktok_shop.client import TikTokShopClient
from platforms.tiktok_shop.schemas import (
    InventoryItem,
    ProductItem,
    flatten_inventory,
    normalize_products,
)
from services.inventory_store import upsert_inventory_items
from services.product_store import upsert_products
from services.sync_state import record_raw_response, upsert_cursor

RESOURCE = "inventory"
PRODUCTS_PATH = "/product/202309/products/search"
INVENTORY_PATH = "/product/202309/inventory/search"


@task(name="fetch-tiktok-products", retries=3, retry_delay_seconds=60)
def fetch_product_index(
    *,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> tuple[list[str], dict[str, str], list[dict[str, Any]]]:
    """枚举全店商品，返回 (product_ids, {product_id: title}, products[] 原始)。

    products[] 原始一并返回，供 save_products 落库（零额外 API 调用）。
    """
    client = TikTokShopClient(
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    products = client.list_products()
    product_ids = [p["id"] for p in products if p.get("id")]
    titles = {p["id"]: p.get("title") for p in products if p.get("id")}
    return product_ids, titles, products


@task(name="save-products-to-db", retries=2, retry_delay_seconds=30)
def save_products(
    products: list[dict[str, Any]],
    *,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> int:
    """清洗并幂等 upsert 商品主数据，记一条 products/search 的 raw 审计。"""
    if not products:
        return 0
    items = [ProductItem.model_validate(row) for row in normalize_products(products)]
    session = SessionLocal()
    try:
        raw_record = record_raw_response(
            session,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            resource="products",
            method="POST",
            path=PRODUCTS_PATH,
            request_body={"enumerate_all": True},
            response_payload={"product_count": len(products)},
            http_status=200,
            business_code="0",
        )
        count = upsert_products(
            session,
            items,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            raw_response_id=raw_record.id,
        )
        session.commit()
        return count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@task(name="fetch-tiktok-inventory", retries=3, retry_delay_seconds=60)
def fetch_inventory(
    product_ids: list[str],
    *,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """按 product_id 分批查询库存，返回合并后的 inventory[]。"""
    if not product_ids:
        return []
    client = TikTokShopClient(
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    return client.search_inventory(product_ids)


@task(name="validate-inventory")
def validate_inventory(
    inventory: list[dict[str, Any]],
    product_titles: dict[str, str],
) -> list[InventoryItem]:
    """展平嵌套响应并 Pydantic 校验清洗。"""
    valid_items = []
    for row in flatten_inventory(inventory, product_titles):
        try:
            valid_items.append(InventoryItem.model_validate(row))
        except Exception as e:  # noqa: BLE001
            print(f"库存校验失败: {e}")
    return valid_items


@task(name="save-inventory-to-db", retries=2, retry_delay_seconds=30)
def save_to_db(
    inventory: list[dict[str, Any]],
    items: list[InventoryItem],
    *,
    product_ids: list[str],
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> int:
    """写入 MySQL（幂等 upsert）并记录 raw payload 与 cursor。"""
    session = SessionLocal()
    try:
        raw_record = record_raw_response(
            session,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            resource=RESOURCE,
            method="POST",
            path=INVENTORY_PATH,
            request_body={"product_id_count": len(product_ids)},
            response_payload={"inventory": inventory},
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
            resource=RESOURCE,
            window_end=datetime.now(timezone.utc),
            extra={"item_count": count, "product_count": len(product_ids)},
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
):
    """库存同步主流程：枚举商品 → 入库商品主数据 → 查库存 → 展平校验 → 入库。"""
    log_egress_ip()
    product_ids, product_titles, products = fetch_product_index(
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    print(f"枚举到 {len(product_ids)} 个商品")

    # 商品主数据顺手入库；失败不阻断库存主链路。
    try:
        product_count = save_products(
            products,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
        )
        print(f"商品主数据入库: {product_count} 个")
    except Exception as e:  # noqa: BLE001
        print(f"商品主数据入库失败（不影响库存同步）: {e}")

    inventory = fetch_inventory(
        product_ids,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    items = validate_inventory(inventory, product_titles)
    count = save_to_db(
        inventory,
        items,
        product_ids=product_ids,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    print(f"库存同步完成: {count} 条记录")
    return count


if __name__ == "__main__":
    from flows._shop_discovery import discover_single_shop

    scope = discover_single_shop()
    print(f"Auto-discovered shop scope: {scope}")
    sync_inventory_flow(**scope)
