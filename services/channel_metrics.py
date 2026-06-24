"""渠道 GMV 拆分（直播/视频/商品卡占比，plan/17 阶段5 渠道饼图）。

数据源：店铺级 `GET /analytics/202509/shop/performance` 的
`data.performance.intervals[].sales.gmv.breakdowns[]`——**实测（2026-06-24 本地真打 hp
同出口）该字段直接按 `type` 拆 LIVE/VIDEO/PRODUCT_CARD，且各项之和=overall**，故一次店铺级
调用即拿到精确三分，无需逐商品 detail、也无需「总减直播减视频」相减兜底（原 plan 的相减法因此
作废，改用此更准更省的单接口拆分）。逐店调用并跨店累加。

兜底：若某店返回里没有 breakdowns（老版本/异常），退回相减法
product_card = max(0, overall − live − video)；未知 type 归入 product_card 以保证总额自洽。

降级：沙箱/无权限/报错时 client.get_shop_performance 返回 None，跳过该店；所有店都拿不到
→ available=False，前端显示「暂无数据」。

实时 + 进程内缓存（TTL 15min，key 含 account_id+shop_ids+窗口），避免每次看板加载都打 API。
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Optional

from core.db import SessionLocal
from core.tenancy import current_account
from models.base_models import PlatformToken
from platforms.tiktok_shop.client import TikTokShopClient

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 15 * 60
# key -> (expires_at_epoch, payload)
_cache: dict[tuple, tuple[float, dict]] = {}

_CHANNEL_LABELS = {
    "live": "直播",
    "video": "视频",
    "product_card": "商品卡",
}


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _discover_shops(
    country: Optional[str], shop_ids: Optional[list[str]]
) -> list[dict]:
    """当前租户（ORM 自动按 account_id 隔离）下的 TikTok 授权店 [{country, shop_id}]。

    analytics 仅 TikTok 有意义，固定 platform=tiktok_shop。shop_ids 给定则收窄到这些店；
    country 给定则再过滤（与看板范围夹紧后的条件对齐）。
    """
    session = SessionLocal()
    try:
        query = (
            session.query(PlatformToken.country, PlatformToken.shop_id)
            .filter(PlatformToken.platform == "tiktok_shop")
            .filter(PlatformToken.shop_id.isnot(None))
        )
        if shop_ids:
            query = query.filter(PlatformToken.shop_id.in_(shop_ids))
        if country:
            query = query.filter(PlatformToken.country == country)
        return [{"country": c, "shop_id": s} for c, s in query.all()]
    finally:
        session.close()


def _parse_shop_channels(
    data: Optional[dict],
) -> tuple[float, float, float, Optional[str]]:
    """解析 shop/performance 的 data 段 → (live, video, product_card, currency)。

    主路径读 intervals[].sales.gmv.breakdowns[]（type ∈ LIVE/VIDEO/PRODUCT_CARD，未知 type
    归 product_card）；若无 breakdowns 则退回 overall 相减兜底。
    """
    if not data:
        return 0.0, 0.0, 0.0, None
    intervals = (data.get("performance") or {}).get("intervals") or []
    live = video = card = overall = 0.0
    currency: Optional[str] = None
    saw_breakdown = False
    for itv in intervals:
        gmv = ((itv.get("sales") or {}).get("gmv")) or {}
        ov = gmv.get("overall") or {}
        overall += _to_float(ov.get("amount"))
        currency = currency or ov.get("currency")
        for b in gmv.get("breakdowns") or []:
            saw_breakdown = True
            amt = _to_float((b.get("gmv") or {}).get("amount"))
            currency = currency or (b.get("gmv") or {}).get("currency")
            typ = b.get("type")
            if typ == "LIVE":
                live += amt
            elif typ == "VIDEO":
                video += amt
            else:  # PRODUCT_CARD + 任何未知 type → 归非内容流量，保证总额自洽
                card += amt
    if not saw_breakdown:
        card = max(0.0, overall - live - video)
    return live, video, card, currency


def get_channel_gmv_breakdown(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
) -> dict:
    """窗口 [start_date, end_date]（闭区间）内整店 GMV 按渠道拆分。

    返回 {"channels": [{key,label,gmv,pct}...], "total_gmv", "currency", "available"}。
    available=False 表示无 analytics 数据（沙箱店 / 无权限 / 全店报错），前端走「暂无数据」。
    platform 入参仅为接口对称（analytics 固定 tiktok_shop），不参与店铺发现过滤。
    """
    account_id = current_account()
    cache_key = (
        account_id,
        tuple(sorted(shop_ids)) if shop_ids else None,
        country,
        start_date,
        end_date,
    )
    now = time.time()
    hit = _cache.get(cache_key)
    if hit and hit[0] > now:
        return hit[1]

    shops = _discover_shops(country, shop_ids)
    live_gmv = video_gmv = card_gmv = 0.0
    currency: Optional[str] = None
    any_data = False

    for shop in shops:
        client = TikTokShopClient(
            country=shop["country"],
            shop_id=shop["shop_id"],
            account_id=account_id,
        )
        data = client.get_shop_performance(start_date, end_date)
        if data is not None:
            any_data = True
        lv, vd, cd, cur = _parse_shop_channels(data)
        live_gmv += lv
        video_gmv += vd
        card_gmv += cd
        currency = currency or cur

    denom = live_gmv + video_gmv + card_gmv

    def _pct(v: float) -> float:
        return round(v / denom * 100, 1) if denom else 0.0

    channels = [
        {"key": k, "label": _CHANNEL_LABELS[k], "gmv": round(v, 2), "pct": _pct(v)}
        for k, v in (
            ("live", live_gmv),
            ("video", video_gmv),
            ("product_card", card_gmv),
        )
    ]
    payload = {
        "channels": channels,
        "total_gmv": round(denom, 2),
        "currency": currency,
        "available": any_data and denom > 0,
    }
    _cache[cache_key] = (now + _CACHE_TTL_SECONDS, payload)
    return payload
