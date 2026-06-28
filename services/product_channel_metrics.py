"""单品渠道 GMV 4 分（达人/自营素材/商品卡/店铺页，看板爆款卡点击展开）。

数据源：单品级 `GET /analytics/202605/shop_products/performance`——每个商品按
「卖家/达人 × 直播/视频/商品卡 + 店铺页」交叉拆好（见 client.get_shop_products_performance）。
本服务把这套字段归并成客户心智模型的 4 分（且各项之和=total）：

    达人(affiliate_total) + 自营素材(seller_live+seller_video)
        + 商品卡(seller_product_card) + 店铺页(shop_tab) = 总 GMV

口径澄清：客户原话「达人渠道/自营素材/直播/商品卡」混了两根正交轴——直播/视频/商品卡是
content_type（销售内容形式，和=100%），达人/自营是 account_type（谁带货，横切视频/直播）。
202605 接口已把两轴交叉拆好，故能凑成上面这个干净的 4 分饼（达人无商品卡/店铺页拆分，按 0）。

逐店调用并按 product_id 累加（多店同 product_id 合并）。进程内缓存整窗列表（TTL 15min，
key 含 account_id+shop_ids+窗口），首次请求暖缓存、后续单品命中。

降级：沙箱/无权限/报错时 client 返回 None，跳过该店；所有店都拿不到 → 该品 available=False，
前端显「该商品暂无渠道数据」（不阻断卡片其它信息）。
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Optional

from core.tenancy import current_account
from platforms.tiktok_shop.client import TikTokShopClient
from services.channel_metrics import _discover_shops

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 15 * 60
# key -> (expires_at_epoch, {product_id: {seg: gmv}, "__currency__": cur})
_cache: dict[tuple, tuple[float, dict]] = {}

_CHANNEL_LABELS = {
    "affiliate": "达人",
    "seller_content": "自营素材",
    "product_card": "商品卡",
    "shop_tab": "店铺页",
}


def _gmv(perf: Optional[dict]) -> tuple[float, Optional[str]]:
    """从单个 *_performance 字段取 attributed_gmv.{amount,currency}；缺字段=0。"""
    if not perf:
        return 0.0, None
    g = perf.get("attributed_gmv") or {}
    try:
        amount = float(g.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return amount, g.get("currency")


def _segments_from_product(p: dict) -> tuple[dict[str, float], Optional[str]]:
    """单个商品 → {affiliate, seller_content, product_card, shop_tab} GMV + 币种。

    affiliate 优先用 affiliate_total_performance；缺则用 affiliate_live+affiliate_video 兜。
    """
    cur: Optional[str] = None

    def take(key: str) -> float:
        nonlocal cur
        v, c = _gmv(p.get(key))
        cur = cur or c
        return v

    aff_total, c = _gmv(p.get("affiliate_total_performance"))
    cur = cur or c
    if not p.get("affiliate_total_performance"):
        aff_total = take("affiliate_live_performance") + take("affiliate_video_performance")

    seg = {
        "affiliate": aff_total,
        "seller_content": take("seller_live_performance") + take("seller_video_performance"),
        "product_card": take("seller_product_card_performance"),
        "shop_tab": take("shop_tab_performance"),
    }
    return seg, cur


def _build_index(
    country: Optional[str], shop_ids: Optional[list[str]], start_date: date, end_date: date
) -> dict:
    """逐店拉 202605 列表并按 product_id 累加 → {product_id: {seg: gmv}}, 附 __currency__/__available__。"""
    shops = _discover_shops(country, shop_ids)
    index: dict[str, dict[str, float]] = {}
    currency: Optional[str] = None
    any_data = False
    for shop in shops:
        client = TikTokShopClient(
            country=shop["country"], shop_id=shop["shop_id"], account_id=current_account()
        )
        products = client.get_shop_products_performance(start_date, end_date)
        if products is None:
            continue
        any_data = True
        for p in products:
            pid = p.get("id")
            if not pid:
                continue
            seg, cur = _segments_from_product(p)
            currency = currency or cur
            acc = index.setdefault(pid, {k: 0.0 for k in _CHANNEL_LABELS})
            for k, v in seg.items():
                acc[k] += v
    index["__currency__"] = currency  # type: ignore[assignment]
    index["__available__"] = any_data  # type: ignore[assignment]
    return index


def get_product_channel_breakdown(
    *,
    product_id: str,
    start_date: date,
    end_date: date,
    country: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
) -> dict:
    """单品渠道 4 分。返回 {channels:[{key,label,gmv,pct}], total_gmv, currency, available}。

    available=False：该窗口下全店无 analytics 数据（沙箱/无权限/报错），或该 product_id 不在
    列表（无渠道归因数据）→ 前端显「该商品暂无渠道数据」。整窗列表进程缓存，按 product_id 命中。
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
        index = hit[1]
    else:
        index = _build_index(country, shop_ids, start_date, end_date)
        _cache[cache_key] = (now + _CACHE_TTL_SECONDS, index)

    any_data = bool(index.get("__available__"))
    currency = index.get("__currency__")
    seg = index.get(product_id)

    denom = sum(seg.values()) if seg else 0.0

    def _pct(v: float) -> float:
        return round(v / denom * 100, 1) if denom else 0.0

    channels = [
        {"key": k, "label": _CHANNEL_LABELS[k], "gmv": round((seg or {}).get(k, 0.0), 2),
         "pct": _pct((seg or {}).get(k, 0.0))}
        for k in ("affiliate", "seller_content", "product_card", "shop_tab")
    ]
    return {
        "channels": channels,
        "total_gmv": round(denom, 2),
        "currency": currency,
        "available": any_data and denom > 0,
    }
