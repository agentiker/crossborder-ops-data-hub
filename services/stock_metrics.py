"""低库存 / 断货预警指标（确定性：可售天数 = 可用库存 ÷ 日均销速）。

数据源：
- inventory 快照表（available_stock 按 sku_id 跨仓/店聚合）。
- 已付款订单销量（order_metrics.get_units_by_sku，近 velocity_window_days 天）折算日均销速。

口径（与业务确认）：
- 日均销速 daily_velocity = 窗口内已付款销量 ÷ velocity_window_days。
- 可售天数 days_of_cover = available_stock ÷ daily_velocity。
- **只盯卖得动的**：daily_velocity == 0（窗口内无销量）的 SKU 不计入预警（死货/下架另议）。
- 分桶：stockout（库存 0 且有销量）> critical（可售 < critical_days）> warning（< warning_days）。
- 可售天数按 sku_id 跨店聚合（与销速 sku 粒度对齐）；展示店取该 SKU 库存最多的店。

公式全部在此用确定性 Python 实现，AI 仅解释结果（不进 HTTP 层、不重算）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from core.config import settings
from core.db import SessionLocal
from core.timezone import OFFSET, business_today
from models.base_models import Inventory, Product
from services.order_metrics import get_units_by_sku


def _classify(available: int, cover: Optional[float], critical_days: int, warning_days: int) -> Optional[str]:
    """返回风险桶名；正常（库存充足）返回 None。仅对有销量的 SKU 调用。"""
    if available <= 0:
        return "stockout"
    if cover is None:
        return None
    if cover < critical_days:
        return "critical"
    if cover < warning_days:
        return "warning"
    return None


def get_stock_risk(
    *,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    critical_days: Optional[int] = None,
    warning_days: Optional[int] = None,
    velocity_window_days: Optional[int] = None,
    include_all: bool = False,
) -> dict:
    """返回低库存/断货风险 SKU + 分桶计数 + 快照时间（供 AI 解释 / 主动推送）。

    两套口径：
    - include_all=False（默认，**监控告警**用）：items 仅含有销量(velocity>0)且落入风险桶的
      SKU，按 days_of_cover 升序（断货排最前）。这是"只盯卖得动快断货"的告警口径。
    - include_all=True（**报告展示**用）：items 含全部在库 SKU，按可售天数升序（断货最前、库存
      充足居中、近期无销量 idle 排末尾，idle 内按库存升序让低库存的先冒头），bucket 取
      stockout/critical/warning/ok（充足）/idle（无销量）。无论哪种口径，buckets 计数始终只算
      真实风险桶（与告警一致），供"断货风险数"KPI 用。
    """
    critical_days = critical_days or settings.stock_cover_critical_days
    warning_days = warning_days or settings.stock_cover_warning_days
    velocity_window_days = velocity_window_days or settings.stock_velocity_window_days

    # 销速窗口：近 velocity_window_days 天（含今天），按印尼业务日。
    end_d = business_today()
    start_d = end_d - timedelta(days=velocity_window_days - 1)
    units_by_sku = get_units_by_sku(
        start_date=start_d,
        end_date=end_d,
        platform=platform,
        country=country,
        shop_ids=shop_ids,
    )

    # 库存按 sku_id 聚合（跨仓/店求和），记展示店（库存最多的店）与商品名、最新快照时间。
    session = SessionLocal()
    try:
        query = session.query(Inventory)
        if platform:
            query = query.filter(Inventory.platform == platform)
        if country:
            query = query.filter(Inventory.country == country)
        if shop_ids:
            query = query.filter(Inventory.shop_id.in_(shop_ids))
        rows = query.all()
    finally:
        session.close()

    agg: dict[str, dict] = {}
    snapshot_at: Optional[datetime] = None
    for r in rows:
        sku_id = r.sku_id
        if not sku_id:
            continue
        stock = r.available_stock or 0
        entry = agg.setdefault(
            sku_id,
            {
                "available": 0,
                "product_name": r.product_name,
                "sku_name": r.sku_name,
                "product_id": r.product_id,
                "top_shop": None,
                "top_shop_stock": -1,
            },
        )
        entry["available"] += stock
        if r.product_name and not entry["product_name"]:
            entry["product_name"] = r.product_name
        if r.sku_name and not entry["sku_name"]:
            entry["sku_name"] = r.sku_name
        if r.product_id and not entry["product_id"]:
            entry["product_id"] = r.product_id
        if stock > entry["top_shop_stock"]:
            entry["top_shop_stock"] = stock
            entry["top_shop"] = r.shop_id
        if r.synced_at is not None and (snapshot_at is None or r.synced_at > snapshot_at):
            snapshot_at = r.synced_at

    # 商品主图（product 级）：收集本批 product_id 一次性批量查 Product，避免逐 SKU N+1。
    # 带 scope 过滤锁定本店，不裸查全表（多租户安全）。
    image_by_pid: dict[str, Optional[str]] = {}
    product_ids = {e["product_id"] for e in agg.values() if e.get("product_id")}
    if product_ids:
        session = SessionLocal()
        try:
            pq = session.query(Product.product_id, Product.main_image_url).filter(
                Product.product_id.in_(product_ids)
            )
            if platform:
                pq = pq.filter(Product.platform == platform)
            if country:
                pq = pq.filter(Product.country == country)
            if shop_ids:
                pq = pq.filter(Product.shop_id.in_(shop_ids))
            for pid, img in pq.all():
                if img and not image_by_pid.get(pid):
                    image_by_pid[pid] = img
        finally:
            session.close()

    buckets = {"stockout": 0, "critical": 0, "warning": 0, "total": 0}
    items: list[dict] = []
    for sku_id, entry in agg.items():
        units = units_by_sku.get(sku_id, 0)
        available = entry["available"]
        daily_velocity = units / velocity_window_days
        cover = (available / daily_velocity) if daily_velocity > 0 else None

        # 风险桶（仅有销量的 SKU 参与）→ 始终用于 buckets 计数（告警口径，不受 include_all 影响）
        risk_bucket = _classify(available, cover, critical_days, warning_days) if units > 0 else None
        if risk_bucket is not None:
            buckets[risk_bucket] += 1
            buckets["total"] += 1

        if not include_all:
            # 告警口径：只留有销量且落风险桶的
            if risk_bucket is None:
                continue
            display_bucket = risk_bucket
            cover_display = round(cover, 1) if cover is not None else 0.0
        else:
            # 报告展示口径：全量，给每个 SKU 一个展示桶
            if available <= 0:
                display_bucket = "stockout"
                cover_display = 0.0
            elif units <= 0:
                display_bucket = "idle"          # 近期无销量
                cover_display = None
            elif risk_bucket is not None:
                display_bucket = risk_bucket
                cover_display = round(cover, 1) if cover is not None else 0.0
            else:
                display_bucket = "ok"            # 库存充足
                cover_display = round(cover, 1) if cover is not None else None

        items.append(
            {
                "sku_id": sku_id,
                "sku_name": entry.get("sku_name"),
                "product_name": entry["product_name"],
                "image_url": image_by_pid.get(entry.get("product_id")),
                "shop_id": entry["top_shop"],
                "available_stock": available,
                "daily_velocity": round(daily_velocity, 2),
                "days_of_cover": cover_display,
                "bucket": display_bucket,
            }
        )

    # 排序：断货(available<=0)最前，其余按可售天数升序，无销量(cover=None)排末尾、其内按库存升序
    # （让低库存的无销量 SKU 先冒头）。
    def _sort_key(i):
        if i["available_stock"] <= 0:
            return (-1.0, i["available_stock"])
        if i["days_of_cover"] is None:
            return (float("inf"), i["available_stock"])
        return (i["days_of_cover"], i["available_stock"])

    items.sort(key=_sort_key)

    return {
        "items": items,
        "buckets": buckets,
        "snapshot_at": (snapshot_at + OFFSET).isoformat() if snapshot_at else None,
        "critical_days": critical_days,
        "warning_days": warning_days,
        "velocity_window_days": velocity_window_days,
    }
