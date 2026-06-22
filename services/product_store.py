"""Product master persistence helpers with idempotent upsert semantics."""
from __future__ import annotations

from collections.abc import Iterable

from core.domain import DomainProduct
from models.base_models import Product
from services.scoping import build_product_key


def scope_filters(model, *, platform, country, shop_id, seller_id, account_id):
    """Build equality filters for a single store scope, mapping None -> IS NULL.

    用于 prune 时精确锁定「本店」，避免误删同租户其他店/其他租户的行。
    """
    pairs = [
        (model.platform, platform),
        (model.country, country),
        (model.shop_id, shop_id),
        (model.seller_id, seller_id),
        (model.account_id, account_id),
    ]
    return [col.is_(None) if val is None else col == val for col, val in pairs]


def upsert_products(
    session,
    items: list[DomainProduct],
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


def prune_products_not_in(
    session,
    active_product_ids: Iterable[str],
    *,
    platform: str = "tiktok_shop",
    country: str = "GLOBAL",
    shop_id: str | None = None,
    seller_id: str | None = None,
    account_id: str | None = None,
) -> int:
    """删除本店 products 中 product_id 不在 active 集合的行（清退草稿/下架等非在售）。

    护栏：active_product_ids 为空时直接返回 0、不删任何行——防止上游 API 异常返回空
    把整店商品清空（对齐报告侧零数据护栏）。返回删除行数。
    """
    active = {str(pid) for pid in active_product_ids if pid}
    if not active:
        return 0
    filters = scope_filters(
        Product,
        platform=platform,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    return (
        session.query(Product)
        .filter(*filters, Product.product_id.notin_(active))
        .delete(synchronize_session=False)
    )
