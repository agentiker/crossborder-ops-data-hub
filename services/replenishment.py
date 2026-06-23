"""补货计划计算（阶段1 核心公式）。

公式：目标备货 = 近 velocity_days 天已付款销量 × 系数（普通/超级爆品）；
      补货量 = ⌈目标备货⌉ − 可用库存 − 在途，≤0 剔除。
系数/超级爆品名单来自 replenishment_config（运营可配）；款号-颜色-尺码 来自 sku_variants；
在途 MVP 按 0（马帮未接通，预留 intransit_by_sku 入口）。输出按补货量降序。

只评估「近窗口有销量」的 SKU（无销量目标=0、必被剔除），故从 get_units_by_sku 出发即可。
"""
from __future__ import annotations

import math
from datetime import timedelta
from typing import Optional

from sqlalchemy import func

from core.db import SessionLocal
from core.timezone import business_today
from models.base_models import Inventory, SkuVariant
from services.order_metrics import get_units_by_sku
from services.replenishment_config import (
    get_effective_config,
    get_super_hot_product_ids,
)


def _available_by_sku(
    session, *, platform, country, shop_id, shop_ids
) -> dict[str, int]:
    """当前库存快照按 sku_id 汇总可用库存（跨仓求和）。"""
    q = session.query(
        Inventory.sku_id, func.coalesce(func.sum(Inventory.available_stock), 0)
    )
    if platform:
        q = q.filter(Inventory.platform == platform)
    if country:
        q = q.filter(Inventory.country == country)
    if shop_ids:
        q = q.filter(Inventory.shop_id.in_(shop_ids))
    elif shop_id:
        q = q.filter(Inventory.shop_id == shop_id)
    q = q.group_by(Inventory.sku_id)
    return {sku_id: int(avail or 0) for sku_id, avail in q.all() if sku_id}


def _variants_by_sku(session, sku_ids: list[str]) -> dict[str, SkuVariant]:
    if not sku_ids:
        return {}
    rows = session.query(SkuVariant).filter(SkuVariant.sku_id.in_(sku_ids)).all()
    return {r.sku_id: r for r in rows}


def compute_replenishment(
    *,
    account_id: Optional[str] = None,
    scope_key: Optional[str] = None,
    platform: Optional[str] = "tiktok_shop",
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    intransit_by_sku: Optional[dict[str, int]] = None,
    session=None,
) -> list[dict]:
    """计算补货建议，返回按补货量降序的行列表。

    每行：sku_id/product_id/product_name/color/size/seller_sku/units/available/intransit/
    multiplier/is_super_hot/target/replenish_qty。
    intransit_by_sku：在途数量（MVP 传 None=全 0；马帮接通后注入 availableStock/shippingQuantity）。
    """
    intransit_by_sku = intransit_by_sku or {}
    own_session = session is None
    session = session or SessionLocal()
    try:
        cfg = get_effective_config(session, account_id=account_id, scope_key=scope_key)
        super_hot = get_super_hot_product_ids(session, account_id=account_id)

        today = business_today()
        # 近 velocity_days 个完整业务日（截至昨天，避免今日 intraday 半天数据）
        end_date = today - timedelta(days=1)
        start_date = today - timedelta(days=cfg.velocity_days)
        units_by_sku = get_units_by_sku(
            start_date=start_date,
            end_date=end_date,
            platform=platform,
            country=country,
            shop_id=shop_id,
            shop_ids=shop_ids,
            session=session,
        )
        if not units_by_sku:
            return []

        available = _available_by_sku(
            session, platform=platform, country=country, shop_id=shop_id, shop_ids=shop_ids
        )
        variants = _variants_by_sku(session, list(units_by_sku.keys()))

        rows: list[dict] = []
        for sku_id, units in units_by_sku.items():
            variant = variants.get(sku_id)
            product_id = variant.product_id if variant else None
            is_super_hot = bool(product_id and product_id in super_hot)
            multiplier = cfg.superhot_multiplier if is_super_hot else cfg.normal_multiplier

            target = math.ceil(units * multiplier)
            avail = available.get(sku_id, 0)
            intransit = int(intransit_by_sku.get(sku_id, 0))
            replenish_qty = target - avail - intransit
            if replenish_qty <= 0:
                continue

            rows.append({
                "sku_id": sku_id,
                "product_id": product_id,
                "product_name": variant.product_name if variant else None,
                "color": variant.color if variant else None,
                "size": variant.size if variant else None,
                "seller_sku": variant.seller_sku if variant else None,
                "units": units,
                "available": avail,
                "intransit": intransit,
                "multiplier": multiplier,
                "is_super_hot": is_super_hot,
                "target": target,
                "replenish_qty": replenish_qty,
            })

        rows.sort(key=lambda r: r["replenish_qty"], reverse=True)
        return rows
    finally:
        if own_session:
            session.close()
