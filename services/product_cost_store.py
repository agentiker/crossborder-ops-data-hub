"""产品成本主数据（RMB 含运费）的录入与取数。

- `product_costs`：当前快照（admin 页/CLI/马帮 flow upsert）；`get_cost_map` 取最新值。
- `product_cost_history`：时间维度历史，成本变化才追加一行（append-on-change）；
  `get_cost_map_asof` 按业务日取「该日生效成本」，供利润聚合精确核算成本涨跌。
成本以 RMB 录入、不折算。马帮同步见 flows/sync_mabang_costs。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from core.db import SessionLocal
from core.timezone import business_today
from models.base_models import ProductCost, ProductCostHistory


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


def _parse_cost(raw) -> Optional[Decimal]:
    """把行里的成本值转 Decimal；非数字/负数返回 None。"""
    try:
        cost = Decimal(str(raw).strip())
    except (InvalidOperation, AttributeError, TypeError):
        return None
    return cost if cost >= 0 else None


def record_cost_history(
    session,
    rows: list[dict],
    *,
    account_id: Optional[str],
    platform: str = "tiktok_shop",
    effective_from: Optional[date] = None,
) -> dict:
    """append-on-change 记成本历史：逐 SKU 比对最新历史，变了才加/更新 effective_from 当日行。

    rows 每行须含 seller_sku/unit_cost_rmb（同 import_costs_from_rows）。坏行静默跳过
    （由 import_costs_from_rows 侧统一收集 errors）。由调用方 commit。
    返回 {added, updated, unchanged}。
    """
    eff = effective_from or business_today()
    added = updated = unchanged = 0
    for row in rows:
        sku = (row.get("seller_sku") or "").strip()
        cost = _parse_cost(row.get("unit_cost_rmb"))
        if not sku or cost is None:
            continue
        latest = (
            session.query(ProductCostHistory)
            .filter_by(account_id=account_id, platform=platform, seller_sku=sku)
            .order_by(ProductCostHistory.effective_from.desc())
            .first()
        )
        note = row.get("note") or None
        if latest is None:
            session.add(ProductCostHistory(
                account_id=account_id, platform=platform, seller_sku=sku,
                unit_cost_rmb=cost, effective_from=eff, note=note,
            ))
            added += 1
        elif latest.effective_from == eff:
            # 同一生效日重复导入：修正当日行（值变了才算 updated）
            if latest.unit_cost_rmb != cost:
                latest.unit_cost_rmb = cost
                latest.note = note
                updated += 1
            else:
                unchanged += 1
        elif latest.unit_cost_rmb != cost:
            # 成本较最新历史发生变化 → 追加新生效日行
            session.add(ProductCostHistory(
                account_id=account_id, platform=platform, seller_sku=sku,
                unit_cost_rmb=cost, effective_from=eff, note=note,
            ))
            added += 1
        else:
            unchanged += 1
    session.flush()
    return {"added": added, "updated": updated, "unchanged": unchanged}


def import_costs_from_rows(
    session,
    rows: list[dict],
    *,
    account_id: Optional[str],
    platform: str = "tiktok_shop",
    effective_from: Optional[date] = None,
) -> dict:
    """批量导入成本（CSV 解析后的 dict 行）。每行须含 seller_sku/unit_cost_rmb，可选 note。

    同时：① upsert product_costs 当前快照 ② append-on-change 记 product_cost_history
    （effective_from 默认业务今日）。返回 {inserted, updated, errors, history}。坏行
    （缺 sku/成本非数字/负数）收集进 errors 不中断。由调用方 commit。
    """
    inserted = updated = 0
    errors: list[dict] = []
    valid_rows: list[dict] = []
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
        valid_rows.append({"seller_sku": sku, "unit_cost_rmb": cost, "note": row.get("note")})
    history = record_cost_history(
        session, valid_rows, account_id=account_id, platform=platform,
        effective_from=effective_from,
    )
    return {"inserted": inserted, "updated": updated, "errors": errors, "history": history}


def get_cost_map(
    *,
    account_id: Optional[str],
    platform: str = "tiktok_shop",
    session=None,
) -> dict[str, Decimal]:
    """返回 {seller_sku: unit_cost_rmb}（当前快照，全部成本）。历史口径见 get_cost_map_asof。"""
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


def get_cost_map_asof(
    *,
    account_id: Optional[str],
    platform: str = "tiktok_shop",
    metric_date: date,
    session=None,
) -> dict[str, Decimal]:
    """返回 {seller_sku: metric_date 当日生效成本}。每 SKU 三级兜底：

    1. effective_from <= metric_date 中最新一行；否则
    2. 该 SKU 最早历史行（记录开始前的日期用最早已知成本估算）；否则
    3. 回落 product_costs 当前值（历史表无该 SKU，如整体尚未 seed）。
    """
    own = session is None
    session = session or SessionLocal()
    try:
        by_sku: dict[str, list[tuple[date, Decimal]]] = {}
        hist = (
            session.query(
                ProductCostHistory.seller_sku,
                ProductCostHistory.effective_from,
                ProductCostHistory.unit_cost_rmb,
            )
            .filter_by(account_id=account_id, platform=platform)
            .all()
        )
        for sku, eff, cost in hist:
            if sku:
                by_sku.setdefault(sku, []).append((eff, Decimal(str(cost))))
        result: dict[str, Decimal] = {}
        for sku, items in by_sku.items():
            items.sort(key=lambda x: x[0])
            asof = [c for eff, c in items if eff <= metric_date]
            result[sku] = asof[-1] if asof else items[0][1]
        # 回落当前快照（覆盖历史表里没有的 SKU）
        cur = (
            session.query(ProductCost.seller_sku, ProductCost.unit_cost_rmb)
            .filter_by(account_id=account_id, platform=platform)
            .all()
        )
        for sku, cost in cur:
            if sku and sku not in result:
                result[sku] = Decimal(str(cost))
        return result
    finally:
        if own:
            session.close()
