"""产品成本主数据（RMB 含运费）的录入与取数（阶段3a，CSV 导入）。

利润公式「产品成本」项的数据源。MVP 用 seller_sku 关联（同 account 多店共用 SKU 成本）。
成本以 RMB 录入、不折算。马帮开通后（阶段4）改由 stock-do-search-sku-list-new.defaultCost 同步。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional

from core.db import SessionLocal
from models.base_models import ProductCost


def upsert_product_cost(
    session,
    *,
    account_id: Optional[str],
    platform: str = "tiktok_shop",
    seller_sku: str,
    unit_cost_rmb: Decimal,
    note: Optional[str] = None,
) -> ProductCost:
    """按 (account_id, platform, seller_sku) 幂等 upsert 单条成本。flush 不 commit。"""
    existing = (
        session.query(ProductCost)
        .filter_by(account_id=account_id, platform=platform, seller_sku=seller_sku)
        .first()
    )
    if existing:
        existing.unit_cost_rmb = unit_cost_rmb
        if note is not None:
            existing.note = note
        result = existing
    else:
        result = ProductCost(
            account_id=account_id,
            platform=platform,
            seller_sku=seller_sku,
            unit_cost_rmb=unit_cost_rmb,
            note=note,
        )
        session.add(result)
    session.flush()
    return result


def import_costs_from_rows(
    session,
    rows: list[dict],
    *,
    account_id: Optional[str],
    platform: str = "tiktok_shop",
) -> dict:
    """批量导入成本（CSV 解析后的 dict 行）。每行须含 seller_sku/unit_cost_rmb，可选 note。

    返回 {inserted, updated, errors:[{row, reason}]}。坏行（缺 sku/成本非数字/负数）收集进 errors
    不中断。由调用方 commit。
    """
    inserted = updated = 0
    errors: list[dict] = []
    for i, row in enumerate(rows):
        sku = (row.get("seller_sku") or "").strip()
        raw_cost = row.get("unit_cost_rmb")
        if not sku:
            errors.append({"row": i + 1, "reason": "缺 seller_sku"})
            continue
        try:
            cost = Decimal(str(raw_cost).strip())
        except (InvalidOperation, AttributeError, TypeError):
            errors.append({"row": i + 1, "reason": f"unit_cost_rmb 非数字: {raw_cost!r}"})
            continue
        if cost < 0:
            errors.append({"row": i + 1, "reason": f"unit_cost_rmb 为负: {cost}"})
            continue
        exists = (
            session.query(ProductCost.id)
            .filter_by(account_id=account_id, platform=platform, seller_sku=sku)
            .first()
        )
        upsert_product_cost(
            session,
            account_id=account_id,
            platform=platform,
            seller_sku=sku,
            unit_cost_rmb=cost,
            note=(row.get("note") or None),
        )
        if exists:
            updated += 1
        else:
            inserted += 1
    return {"inserted": inserted, "updated": updated, "errors": errors}


def get_cost_map(
    *,
    account_id: Optional[str],
    platform: str = "tiktok_shop",
    session=None,
) -> dict[str, Decimal]:
    """返回 {seller_sku: unit_cost_rmb}（该 account 全部成本，供利润聚合 join）。"""
    own = session is None
    session = session or SessionLocal()
    try:
        rows = (
            session.query(ProductCost.seller_sku, ProductCost.unit_cost_rmb)
            .filter_by(account_id=account_id, platform=platform)
            .all()
        )
        return {sku: Decimal(str(cost)) for sku, cost in rows if sku}
    finally:
        if own:
            session.close()
