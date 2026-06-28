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

# 细分：达人按 直播/视频/其它(残差) 拆，自营按 直播/视频 拆；商品卡/店铺页本就是叶子。
# 「达人其它」= affiliate_total - live - video（达人商品卡/店铺页等未单列形式），仅在 >0 时显示。
_FINE_LABELS = {
    "affiliate_live": "达人直播",
    "affiliate_video": "达人视频",
    "affiliate_other": "达人其它",
    "seller_live": "自营直播",
    "seller_video": "自营视频",
    "product_card": "商品卡",
    "shop_tab": "店铺页",
}

# 细→粗 归并表：细分各项之和 == 对应粗分项，故两种粒度总额一致。
_COARSE_OF = {
    "affiliate_live": "affiliate",
    "affiliate_video": "affiliate",
    "affiliate_other": "affiliate",
    "seller_live": "seller_content",
    "seller_video": "seller_content",
    "product_card": "product_card",
    "shop_tab": "shop_tab",
}


def _gmv(perf: Optional[dict], *keys: str) -> tuple[float, Optional[str]]:
    """从单个 *_performance 块按候选键取 GMV.{amount,currency}；缺字段=0。

    真打验证(prod 真实非零数据,2026-06-28):各块 GMV 字段名并不统一——
    affiliate_live=live_attributed_gmv、affiliate_video=attributed_video_gmv、
    shop_tab=shop_tab_gmv，其余(seller_*/affiliate_total)才是 attributed_gmv。
    故按块传入候选键，取首个命中（缺省退回 attributed_gmv 兼容沙箱/合成数据）。
    """
    if not perf:
        return 0.0, None
    candidates = keys or ("attributed_gmv",)
    for k in candidates:
        g = perf.get(k)
        if g:
            try:
                return float(g.get("amount") or 0), g.get("currency")
            except (TypeError, ValueError):
                return 0.0, g.get("currency")
    return 0.0, None


def _segments_from_product(p: dict) -> tuple[dict[str, float], Optional[str]]:
    """单个商品 → 细分 7 键 {affiliate_live, affiliate_video, affiliate_other, seller_live,
    seller_video, product_card, shop_tab} GMV + 币种（粗分由调用方按 _COARSE_OF 归并）。

    达人口径以 affiliate_total 为准（含 live/video 之外的其它达人形式）；live/video 为已知子项，
    差额(total-live-video)计入"达人其它"残差，保证 细分之和 == 粗分达人。无 total 时退回 live+video。
    注意：各渠道是多触点归因(可重叠)，故 4 渠道之和未必等于总 GMV——本服务做的是"渠道构成占比"，
    非真 GMV 分割；donut 按各切片之和归一(每个切片是其在归因总额中的占比)。
    """
    cur: Optional[str] = None

    def take(key: str, *gmv_keys: str) -> float:
        nonlocal cur
        v, c = _gmv(p.get(key), *gmv_keys)
        cur = cur or c
        return v

    aff_live = take("affiliate_live_performance", "live_attributed_gmv", "attributed_gmv")
    aff_video = take("affiliate_video_performance", "attributed_video_gmv", "attributed_gmv")
    aff_total = take("affiliate_total_performance", "attributed_gmv")
    if not aff_total:
        aff_total = aff_live + aff_video
    aff_other = max(aff_total - aff_live - aff_video, 0.0)

    seg = {
        "affiliate_live": aff_live,
        "affiliate_video": aff_video,
        "affiliate_other": aff_other,
        "seller_live": take("seller_live_performance", "attributed_gmv"),
        "seller_video": take("seller_video_performance", "attributed_gmv"),
        "product_card": take("seller_product_card_performance", "attributed_gmv"),
        "shop_tab": take("shop_tab_performance", "shop_tab_gmv", "attributed_gmv"),
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
            acc = index.setdefault(pid, {k: 0.0 for k in _FINE_LABELS})
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
    """单品渠道分布。返回 {channels(粗4), fine(细6), total_gmv, currency, available}。

    channels=粗分 4（达人/自营素材/商品卡/店铺页），fine=细分 6（达人直播/达人视频/自营直播/
    自营视频/商品卡/店铺页）；两种粒度总额一致，前端可切换。

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
    fine_seg = index.get(product_id) or {k: 0.0 for k in _FINE_LABELS}

    # 细→粗 归并
    coarse_seg: dict[str, float] = {}
    for fk, v in fine_seg.items():
        coarse_seg[_COARSE_OF[fk]] = coarse_seg.get(_COARSE_OF[fk], 0.0) + v

    denom = sum(fine_seg.values())

    def _pct(v: float) -> float:
        return round(v / denom * 100, 1) if denom else 0.0

    channels = [
        {"key": k, "label": _CHANNEL_LABELS[k], "gmv": round(coarse_seg.get(k, 0.0), 2),
         "pct": _pct(coarse_seg.get(k, 0.0))}
        for k in ("affiliate", "seller_content", "product_card", "shop_tab")
    ]
    fine = [
        {"key": k, "label": _FINE_LABELS[k], "gmv": round(fine_seg.get(k, 0.0), 2),
         "pct": _pct(fine_seg.get(k, 0.0))}
        for k in ("affiliate_live", "affiliate_video", "affiliate_other",
                  "seller_live", "seller_video", "product_card", "shop_tab")
    ]
    return {
        "channels": channels,
        "fine": fine,
        "total_gmv": round(denom, 2),
        "currency": currency,
        "available": any_data and denom > 0,
    }
