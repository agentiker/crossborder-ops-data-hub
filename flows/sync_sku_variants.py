"""SKU 变体同步 flow（颜色/尺码主数据）。

补货采购单需「款号-颜色-尺码」，但 products/search 不返回变体属性，须逐个 Get Product 取
data.skus[].sales_attributes。本 flow 遍历在售商品（list_products，默认 ACTIVATE）逐个
get_product，解析颜色/尺码入 sku_variants（快照式：全量覆盖 + prune 清退下架/删除变体）。

定时低频跑即可（变体属性极少变；商品数不大）。与 sync_inventory 各自独立（后者也逐商品
get_product 但只取价，本 flow 取变体属性，互不依赖）。
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from prefect import flow, task

logger = logging.getLogger(__name__)

from core.db import SessionLocal
from flows.network import log_egress_ip
from platforms.tiktok_shop.client import PLATFORM as TIKTOK_PLATFORM
from platforms.tiktok_shop.client import TikTokShopClient
from services.sku_variant_store import (
    parse_sku_variants,
    prune_sku_variants_not_in,
    upsert_sku_variants,
)
from services.sync_state import record_raw_response, upsert_cursor

RESOURCE = "sku_variants"
PRODUCT_DETAIL_PATH = "/product/202309/products/{product_id}"


@task(name="fetch-sku-variant-details", retries=3, retry_delay_seconds=60)
def fetch_variant_details(
    *,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> tuple[dict[str, str], dict[str, dict]]:
    """枚举在售商品逐个取详情，返回 (titles, {product_id: get_product_data})。

    单商品详情失败只跳过（不阻断整体；该商品本次无变体，prune 不会误删——见下方 save 说明）。
    """
    client = TikTokShopClient(
        country=country, shop_id=shop_id, seller_id=seller_id, account_id=account_id
    )
    products = client.list_products()
    titles = {p["id"]: p.get("title") for p in products if p.get("id")}
    details: dict[str, dict] = {}
    for pid in titles.keys():
        try:
            details[pid] = client.get_product(pid)
        except Exception as e:  # noqa: BLE001
            print(f"商品 {pid} 详情失败，跳过变体: {e}")
    return titles, details


@task(name="normalize-sku-variants")
def normalize_variants(titles: dict[str, str], details: dict[str, dict]) -> list:
    """解析 sales_attributes → DomainSkuVariant 列表。委托 sku_variant_store.parse_sku_variants。"""
    return parse_sku_variants(details, titles)


@task(name="save-sku-variants", retries=2, retry_delay_seconds=30)
def save_variants(
    items: list,
    *,
    product_count: int,
    detail_count: int,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> int:
    """单事务幂等 upsert + prune 清退 + 记 raw 审计 + cursor。

    护栏：本次解析到 0 个变体时不 prune（防 list/get 全失败把全店变体清空）。
    """
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
            method="GET",
            path=PRODUCT_DETAIL_PATH,
            request_params={"status": "ACTIVATE"},
            request_body={"product_count": product_count, "detail_count": detail_count},
            response_payload={"variant_count": len(items)},
            http_status=200,
            business_code="0",
        )
        count = upsert_sku_variants(
            session,
            items,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            raw_response_id=raw_record.id,
        )
        pruned = prune_sku_variants_not_in(
            session,
            items,
            platform=TIKTOK_PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
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
            extra={"variant_count": count, "pruned": pruned, "product_count": product_count},
        )
        session.commit()
        return count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@flow(name="tiktok-sku-variant-sync", log_prints=True)
def sync_sku_variants_flow(
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
):
    """SKU 变体同步主流程。"""
    log_egress_ip()
    titles, details = fetch_variant_details(
        country=country, shop_id=shop_id, seller_id=seller_id, account_id=account_id
    )
    items = normalize_variants(titles, details)
    count = save_variants(
        items,
        product_count=len(titles),
        detail_count=len(details),
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    print(f"SKU 变体同步完成: {count} 条变体（{len(titles)} 个在售商品）")
    return count


if __name__ == "__main__":
    from flows._shop_discovery import run_for_all_shops

    run_for_all_shops(sync_sku_variants_flow)
