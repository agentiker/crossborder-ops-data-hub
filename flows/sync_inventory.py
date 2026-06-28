"""Inventory sync flow.

两步流程：先 products/search 枚举全店商品（拿 product_id 与 title），再分批
inventory/search 查库存。inventory/search 必须按 product_id 查、本身无翻页，故依赖
products 枚举。响应嵌套 inventory[].skus[].warehouse_inventory[]，展平为每个
SKU×仓库一行后幂等 upsert。
"""

from datetime import datetime, timezone
from typing import Any, Optional

from core.retry import retry

from core.db import SessionLocal
from flows.network import log_egress_ip
from core.domain import DomainInventoryItem
from platforms.tiktok_shop.client import PLATFORM as TIKTOK_PLATFORM
from platforms.tiktok_shop.client import TikTokShopClient
from platforms.tiktok_shop.normalize import to_domain_inventory, to_domain_products
from services.inventory_store import prune_inventory_not_in, upsert_inventory_items
from services.product_store import prune_products_not_in, upsert_products
from services.sync_state import record_raw_response, upsert_cursor

RESOURCE = "inventory"
PRODUCTS_PATH = "/product/202309/products/search"
INVENTORY_PATH = "/product/202309/inventory/search"


@retry(retries=3, delay_seconds=60)
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


@retry(retries=3, delay_seconds=60)
def fetch_product_prices(
    product_ids: list[str],
    *,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> dict[str, list[dict[str, Any]]]:
    """逐商品取详情，返回 {product_id: skus[]}，供 normalize 取含税 sale_price 算最低价。

    products/search 对本类卖家不返回 sale_price（只回税前价），含税展示价须走商品详情。
    单个商品取价失败只跳过（该商品 min_price 回退到 search 税前价），不阻断整体。
    """
    if not product_ids:
        return {}, {}
    client = TikTokShopClient(
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    prices: dict[str, list[dict[str, Any]]] = {}
    images: dict[str, str] = {}
    for pid in product_ids:
        try:
            detail = client.get_product(pid)
            prices[pid] = detail.get("skus") or []
            thumb = _thumb_url_from_detail(detail)
            if thumb:
                images[pid] = thumb
        except Exception as e:  # noqa: BLE001
            print(f"商品 {pid} 详情取价失败，回退税前价: {e}")
    return prices, images


def _thumb_url_from_detail(detail: dict[str, Any]) -> Optional[str]:
    """从 get_product 详情取主图缩略图 URL（main_images[0].thumb_urls[0]，看板爆款小图）。

    main_images 是商品主图列表，每张含 thumb_urls（缩略图，约 300×300）与 urls（原图）。
    取第一张主图的第一个缩略图；无图返回 None。CDN 无防盗链，可直接 <img src>。
    """
    imgs = detail.get("main_images") or []
    if not imgs:
        return None
    thumbs = imgs[0].get("thumb_urls") or []
    return thumbs[0] if thumbs else None


@retry(retries=2, delay_seconds=30)
def save_products(
    products: list[dict[str, Any]],
    price_skus_by_id: Optional[dict[str, list[dict[str, Any]]]] = None,
    images_by_id: Optional[dict[str, str]] = None,
    *,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> int:
    """清洗并幂等 upsert 商品主数据，记一条 products/search 的 raw 审计。"""
    if not products:
        return 0
    items = to_domain_products(products, price_skus_by_id, images_by_id)
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
            request_body={
                "enumerate_all": True,
                "price_detail_count": len(price_skus_by_id or {}),
            },
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
        # 清退本次未返回（非 ACTIVATE：草稿/下架等）的商品旧行，避免僵尸数据。
        pruned = prune_products_not_in(
            session,
            [it.product_id for it in items],
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
        )
        if pruned:
            print(f"清退非在售商品: {pruned} 个")
        session.commit()
        return count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@retry(retries=3, delay_seconds=60)
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


def validate_inventory(
    inventory: list[dict[str, Any]],
    product_titles: dict[str, str],
) -> list[DomainInventoryItem]:
    """展平嵌套响应并转成平台中立 DomainInventoryItem（校验/容错下沉到 normalize）。"""
    return to_domain_inventory(inventory, product_titles)


@retry(retries=2, delay_seconds=30)
def save_to_db(
    inventory: list[dict[str, Any]],
    items: list[DomainInventoryItem],
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
        # 清退本次未返回的 SKU 旧行（非在售商品的 SKU / 活跃商品被删的变体）。
        pruned = prune_inventory_not_in(
            session,
            items,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
        )
        if pruned:
            print(f"清退非在售库存行: {pruned} 条")
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
        price_skus_by_id, images_by_id = fetch_product_prices(
            product_ids,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
        )
        product_count = save_products(
            products,
            price_skus_by_id,
            images_by_id,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
        )
        print(f"商品主数据入库: {product_count} 个（取价 {len(price_skus_by_id)}/{len(product_ids)}）")
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
    from flows._shop_discovery import run_for_all_shops

    run_for_all_shops(sync_inventory_flow)
