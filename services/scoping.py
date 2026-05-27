"""Helpers for deterministic platform/account scoping."""

from __future__ import annotations

from typing import Optional


UNKNOWN_SCOPE_VALUE = "_"


def normalize_scope_value(value: Optional[object]) -> str:
    """Normalize optional scope fragments for stable unique keys."""
    if value is None:
        return UNKNOWN_SCOPE_VALUE
    text = str(value).strip()
    return text or UNKNOWN_SCOPE_VALUE


def build_scope_key(
    *,
    platform: str,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    resource: Optional[str] = None,
) -> str:
    """Build a deterministic key for account-scoped records."""
    parts = {
        "platform": normalize_scope_value(platform),
        "country": normalize_scope_value(country).upper(),
        "shop": normalize_scope_value(shop_id),
        "seller": normalize_scope_value(seller_id),
        "account": normalize_scope_value(account_id),
        "warehouse": normalize_scope_value(warehouse_id),
    }
    if resource is not None:
        parts["resource"] = normalize_scope_value(resource)
    return "|".join(f"{key}={value}" for key, value in parts.items())


def build_inventory_key(
    *,
    platform: str,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    sku_id: str,
) -> str:
    """Build the idempotency key for an inventory row."""
    return build_scope_key(
        platform=platform,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        warehouse_id=warehouse_id,
        resource=f"inventory_sku:{normalize_scope_value(sku_id)}",
    )
