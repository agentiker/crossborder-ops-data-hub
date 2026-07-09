"""GMV Max 报表响应 → 日级记录的纯解析层（无 IO，好测）。

职责：把 /gmv_max/report/get/ 返回的 data 段（含 list[].dimensions/metrics，值全是字符串）
解析成规整的日级 dict，供 sync flow 落库。刻意不碰 DB / 网络，逻辑全部可用 fixture 单测锁死。

坐实来源（官方响应示例，见 tests/fixtures/gmv_max_report_*.json）：
- 花费字段 = metrics.cost（字符串，如 "125000.00"）；另有 net_cost/gross_revenue/orders/roi/currency
- 按天维度键 = dimensions.stat_time_day（"YYYY-MM-DD"）

时区口径（重要）：报表日期基于**广告账户时区**（第 4 个时区，既非操作者 CST 也非店铺 WIB）。
stat_time_day 已经是该时区下的自然日字符串，本层**原样保留**、不做偏移搬移——是否需要把它
对齐到店铺 WIB 业务日，取决于广告账户时区实际是什么（授权后真打确认），故留给 sync/入库层按
account_tz 决策，解析层只忠实解析，不擅自归日，避免重蹈"误改时区"覆辙（见 memory 时区条）。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional


def _to_decimal(value) -> Decimal:
    """字符串/数字 → Decimal；空/None/非法 → 0（报表 metric 恒为字符串，缺失按 0）。"""
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _parse_stat_day(raw: Optional[str]) -> Optional[date]:
    """dimensions.stat_time_day（"YYYY-MM-DD"）→ date；缺失/非法 → None。"""
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except (ValueError, TypeError):
        return None


def parse_gmv_max_report(
    data: dict,
    *,
    store_id: Optional[str] = None,
    advertiser_id: Optional[str] = None,
) -> list[dict]:
    """把报表 data 段解析成日级记录列表。

    Args:
        data: client.get_gmv_max_report 返回的原始 data 段（含 list[]、page_info）。
        store_id / advertiser_id: 透传标注到每行（报表响应本身不带，靠请求上下文补）。

    Returns:
        每项：
          metric_date(date|None) / stat_day_raw(str) / currency(str|None) /
          cost / net_cost / gross_revenue / orders / roi / cost_per_order(均 Decimal) /
          store_id / advertiser_id / dimensions(原始维度 dict)
        metric_date 为 None 的行（无 stat_time_day 维度，如按 campaign 聚合）保留原样，由调用方决定取舍。
    """
    rows: list[dict] = []
    for item in data.get("list") or []:
        dims = item.get("dimensions") or {}
        metrics = item.get("metrics") or {}
        stat_raw = dims.get("stat_time_day")
        rows.append(
            {
                "metric_date": _parse_stat_day(stat_raw),
                "stat_day_raw": stat_raw,
                "currency": metrics.get("currency"),
                "cost": _to_decimal(metrics.get("cost")),
                "net_cost": _to_decimal(metrics.get("net_cost")),
                "gross_revenue": _to_decimal(metrics.get("gross_revenue")),
                "orders": _to_decimal(metrics.get("orders")),
                "roi": _to_decimal(metrics.get("roi")),
                "cost_per_order": _to_decimal(metrics.get("cost_per_order")),
                "store_id": store_id,
                "advertiser_id": advertiser_id,
                "dimensions": dims,
            }
        )
    return rows


def summarize_cost(rows: list[dict]) -> dict:
    """把日级行汇总成 {currency: 总 cost}（跨币种不混算，与费率/GMV 同口径）。"""
    totals: dict[str, Decimal] = {}
    for r in rows:
        cur = r.get("currency") or "UNKNOWN"
        totals[cur] = totals.get(cur, Decimal("0")) + r.get("cost", Decimal("0"))
    return {k: v for k, v in totals.items()}
