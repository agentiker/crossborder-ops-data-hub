"""待发货预警指标（确定性分桶，公式不进 HTTP 层）。

数据源：pending_fulfillments 快照表（order_status=AWAITING_SHIPMENT 全量快照）。
口径：
- 超时(overdue)：发货截止时间 < now
- 临界(critical)：now <= 发货截止时间 < now + warning_hours（默认 24）
- 正常(normal)：发货截止时间 >= now + warning_hours
- 未知(unknown)：无发货截止时间

时间比较全用 naive UTC（与 _epoch_to_dt 产出口径一致）；呈现转印尼当地时间 UTC+7
（core.timezone.OFFSET）。**分桶判定的 SLA 字段集中在 SLA_FIELD 一处常量**——Go SDK 注释里
tts=最晚揽收、rts=最晚发货，上线时对真实店铺后台"发货截止"核对后改这一处即可切换。
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from core.config import settings
from core.db import SessionLocal
from core.timezone import OFFSET
from models.base_models import PendingFulfillment

# 超时分桶判定用的 SLA 字段（待真实店铺核对，需切换只改此处 + caliber 文案）
SLA_FIELD = "tts_sla_time"


def _now() -> datetime:
    """当前 UTC 时间（naive，与 SLA 字段的存储口径一致）。测试 monkeypatch 此函数。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_float(value) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value or 0)


def _to_local_iso(dt: Optional[datetime]) -> Optional[str]:
    """naive UTC → 印尼当地时间 ISO 串（不带时区，已是 UTC+7 的墙上时间）。"""
    if dt is None:
        return None
    return (dt + OFFSET).isoformat()


def _classify(sla: Optional[datetime], now: datetime, warning_delta) -> str:
    if sla is None:
        return "unknown"
    if sla < now:
        return "overdue"
    if sla < now + warning_delta:
        return "critical"
    return "normal"


def get_pending_fulfillments(
    *,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    warning_hours: Optional[int] = None,
    limit: int = 200,
) -> dict:
    """返回待发货明细 + 超时分桶计数 + 分店汇总（供 AI 解释）。

    counts/by_shop 基于全集统计，items 按 SLA 升序（超时在前、unknown 殿后）后截断 limit。
    """
    from datetime import timedelta

    if warning_hours is None:
        warning_hours = settings.fulfillment_warning_hours
    warning_delta = timedelta(hours=warning_hours)
    now = _now()

    session = SessionLocal()
    try:
        query = session.query(PendingFulfillment)
        if platform:
            query = query.filter(PendingFulfillment.platform == platform)
        if country:
            query = query.filter(PendingFulfillment.country == country)
        if shop_ids:
            query = query.filter(PendingFulfillment.shop_id.in_(shop_ids))
        rows = query.all()
    finally:
        session.close()

    buckets = {"overdue": 0, "critical": 0, "normal": 0, "unknown": 0}
    by_shop: dict[str, dict[str, int]] = {}
    snapshot_at: Optional[datetime] = None
    enriched = []

    for r in rows:
        sla = getattr(r, SLA_FIELD)
        bucket = _classify(sla, now, warning_delta)
        buckets[bucket] += 1

        shop_key = r.shop_id or "_"
        shop_agg = by_shop.setdefault(
            shop_key, {"overdue": 0, "critical": 0, "normal": 0, "unknown": 0, "total": 0}
        )
        shop_agg[bucket] += 1
        shop_agg["total"] += 1

        if r.synced_at is not None and (snapshot_at is None or r.synced_at > snapshot_at):
            snapshot_at = r.synced_at

        hours_left = round((sla - now).total_seconds() / 3600, 1) if sla is not None else None
        enriched.append(
            {
                "row": r,
                "sla": sla,
                "bucket": bucket,
                "hours_left": hours_left,
            }
        )

    # 排序：SLA 升序（超时在前），unknown（无 SLA）殿后
    enriched.sort(key=lambda e: (e["sla"] is None, e["sla"] or datetime.max))

    items = [
        {
            "order_id": e["row"].order_id,
            "shop_id": e["row"].shop_id,
            "order_status": e["row"].order_status,
            "delivery_option_name": e["row"].delivery_option_name,
            "item_count": e["row"].item_count or 0,
            "first_product_name": e["row"].first_product_name,
            "total_amount": _to_float(e["row"].total_amount),
            "currency": e["row"].currency,
            "is_cod": bool(e["row"].is_cod),
            "create_time_local": _to_local_iso(e["row"].create_time),
            "sla_time_local": _to_local_iso(e["sla"]),
            "hours_left": e["hours_left"],
            "bucket": e["bucket"],
        }
        for e in enriched[:limit]
    ]

    buckets["total"] = sum(v for k, v in buckets.items() if k != "total")

    return {
        "items": items,
        "buckets": buckets,
        "by_shop": [
            {"shop_id": shop_id, **agg} for shop_id, agg in sorted(by_shop.items())
        ],
        "snapshot_at": _to_local_iso(snapshot_at),
        "warning_hours": warning_hours,
    }
