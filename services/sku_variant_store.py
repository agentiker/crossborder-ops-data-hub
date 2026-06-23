"""SKU 变体主数据的解析 + 幂等落库（按 sku_id upsert，快照式 prune 清退）。

数据源：Get Product 的 data.skus[].sales_attributes（颜色/尺码等）。解析在 parse_sku_variants，
落库 upsert_sku_variants / 清退 prune_sku_variants_not_in，与 inventory_store 同构。
"""
from __future__ import annotations

from typing import Any, Optional

from core.domain import DomainSkuVariant
from models.base_models import SkuVariant
from services.product_store import scope_filters
from services.scoping import build_sku_variant_key

# 属性名匹配关键词（大小写不敏感）：命中即取该属性的 value_name 作颜色/尺码
_COLOR_KEYS = ("color", "colour", "颜色", "色")
_SIZE_KEYS = ("size", "尺码", "尺寸", "码")


def _match_attr(name: Optional[str], keys: tuple[str, ...]) -> bool:
    if not name:
        return False
    low = name.lower()
    return any(k in low for k in keys)


def parse_sku_variants(
    details: dict[str, dict],
    titles: Optional[dict[str, str]] = None,
) -> list[DomainSkuVariant]:
    """从 {product_id: get_product_data} 解析每个 SKU 的颜色/尺码变体。

    details 每个 value 是 get_product 返回的 data（含 skus[]、可能含 title）。title 优先取
    data.title，回退 titles 映射。无 sku id 的跳过。color/size 按属性名匹配 value_name；
    attributes 存全部 [{name,value_name}] 兜底。
    """
    titles = titles or {}
    out: list[DomainSkuVariant] = []
    for product_id, data in (details or {}).items():
        data = data or {}
        product_name = data.get("title") or titles.get(product_id)
        for sku in data.get("skus") or []:
            sku_id = sku.get("id")
            if not sku_id:
                continue
            color = size = None
            attrs: list[dict] = []
            for attr in sku.get("sales_attributes") or []:
                name = attr.get("name")
                value = attr.get("value_name")
                attrs.append({"name": name, "value_name": value})
                if color is None and _match_attr(name, _COLOR_KEYS):
                    color = value
                elif size is None and _match_attr(name, _SIZE_KEYS):
                    size = value
            out.append(
                DomainSkuVariant(
                    sku_id=str(sku_id),
                    product_id=product_id,
                    seller_sku=sku.get("seller_sku"),
                    product_name=product_name,
                    color=color,
                    size=size,
                    attributes=attrs or None,
                )
            )
    return out


def upsert_sku_variants(
    session,
    items: list[DomainSkuVariant],
    *,
    platform: str = "tiktok_shop",
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    raw_response_id: Optional[int] = None,
) -> int:
    """按 sku_id 幂等 upsert SKU 变体行。"""
    for item in items:
        idempotency_key = build_sku_variant_key(
            platform=platform,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            sku_id=item.sku_id,
        )
        existing = (
            session.query(SkuVariant).filter_by(idempotency_key=idempotency_key).first()
        )
        if existing:
            existing.product_id = item.product_id
            existing.seller_sku = item.seller_sku
            existing.product_name = item.product_name
            existing.color = item.color
            existing.size = item.size
            existing.attributes = item.attributes
            existing.raw_response_id = raw_response_id
        else:
            session.add(
                SkuVariant(
                    platform=platform,
                    country=country,
                    shop_id=shop_id,
                    seller_id=seller_id,
                    account_id=account_id,
                    idempotency_key=idempotency_key,
                    sku_id=item.sku_id,
                    product_id=item.product_id,
                    seller_sku=item.seller_sku,
                    product_name=item.product_name,
                    color=item.color,
                    size=item.size,
                    attributes=item.attributes,
                    raw_response_id=raw_response_id,
                )
            )
    session.flush()
    return len(items)


def prune_sku_variants_not_in(
    session,
    items: list[DomainSkuVariant],
    *,
    platform: str = "tiktok_shop",
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> int:
    """删除本店 sku_variants 中不在本次集合的行（下架商品/已删变体）。空集不删（防误清）。"""
    if not items:
        return 0
    active_keys = {
        build_sku_variant_key(
            platform=platform,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            sku_id=item.sku_id,
        )
        for item in items
    }
    filters = scope_filters(
        SkuVariant,
        platform=platform,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
    )
    return (
        session.query(SkuVariant)
        .filter(*filters, SkuVariant.idempotency_key.notin_(active_keys))
        .delete(synchronize_session=False)
    )
