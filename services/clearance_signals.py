"""清仓嫌疑信号（补货"该不该补"的二次判断）。

补货列表按「卖得快 + 库存低」算，但卖得快 ≠ 该补——也可能商家在清仓甩卖。本模块从现有
订单/SKU数据算几个判别信号（无需新授权），组合出「清仓嫌疑」verdict + reason，供 LLM/卡片
决定要不要在采购单里提醒「补货前与采购确认」。

信号（按判别力）：
  - 折扣加深趋势（主）：近期折扣深度中位值 vs 早期，加深 → 清仓定价特征。比 promotion 活动
    接口更全——订单折扣抓所有实际成交降价（含未登记活动的手动改价）；而 promotion 接口
    （/promotion/202309/activities/search，2026-07-18 已验证授权）只返后台登记的正式活动，
    且 list 不返折扣数值（要逐个 GetActivity）。promotion=运营意图，订单折扣=实际效果。
  - 销量脉冲突刺（辅）：近期日均 vs 早期日均，突刺 → 活动驱动而非有机增长。
  - SKU无销率（辅）：同款其它SKU无销、仅个别在清 → 清尾货指纹（但对「热门款整体清仓」
    无效——那种所有SKU都在快销、无销率低，故只作辅助，不单独定罪）。

阈值均为模块常量，可按真实数据观感调。verdict 保守：只在折扣加深（或「突刺+高无销率」
双满足）时判嫌疑，避免把正常长尾款/常规促销误判成清仓。
"""
from __future__ import annotations

from datetime import timedelta
from statistics import median
from typing import Optional

from sqlalchemy import func

from core.db import SessionLocal
from core.timezone import business_today
from models.base_models import OrderHeader, OrderLineItem, SkuVariant
# 同包复用：scope 过滤 + date→datetime(印尼 UTC+7) 窗口换算，与 get_units_by_sku 同口径。
from services.order_metrics import _paid_window, _scope_filters

# ── 阈值（可调）───────────────────────────────────────────────────────────
DEEPENING_PP = 8.0       # 折扣加深 ≥8pp 判「加深」（近期中位折扣 − 早期中位折扣）
SPIKE_RATIO = 3.0        # 近期日均 / 早期日均 ≥3 判「突刺」
MORTALITY_HIGH = 0.8     # 无销 SKU 占比 ≥80% 判「高」（辅助，不单独定罪）
RECENT_DAYS = 30         # 「近期」窗口天数（与补货 velocity 窗口对齐）
LOOKBACK_DAYS = 60       # 回看总长；早期 = [lookback, recent) 天前


def compute_clearance_signals(
    product_ids: list[str],
    *,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    recent_days: int = RECENT_DAYS,
    lookback_days: int = LOOKBACK_DAYS,
    session=None,
) -> dict[str, dict]:
    """对给定 product_id 算清仓嫌疑信号，返回 {product_id: signals}。

    每个 signals::
        {
          discount_trend: {early_pct, recent_pct, delta_pp, deepening},
          sales_spike:    {recent_daily, prior_daily, ratio, spiking},
          mortality:      {total, selling, dead_rate, high},
          suspect: bool,          # 综合判：是否清仓嫌疑
          reason: str,            # 人话理由（供 LLM/卡片，仅 suspect=True 时有意义）
        }
    数据不足（无早期订单等）的信号字段为 None、不参与判定。
    """
    product_ids = [p for p in product_ids if p]
    if not product_ids:
        return {}
    own_session = session is None
    session = session or SessionLocal()
    prior_days = max(lookback_days - recent_days, 1)
    try:
        today = business_today()
        # 两段窗口（date）→ _paid_window 换算成 naive UTC datetime 边界（归印尼业务日）。
        recent_start, recent_end = _paid_window(
            today - timedelta(days=recent_days - 1), today - timedelta(days=1))
        prior_start, prior_end = _paid_window(
            today - timedelta(days=lookback_days - 1), today - timedelta(days=recent_days))

        sc = dict(platform=platform, country=country, shop_id=shop_id, shop_ids=shop_ids)
        out: dict[str, dict] = {}
        for pid in product_ids:
            disc = _discount_trend(session, pid, prior_start, prior_end, recent_start, recent_end, sc)
            spike = _sales_spike(session, pid, prior_start, prior_end, recent_start, recent_end,
                                 recent_days, prior_days, sc)
            mort = _mortality(session, pid, recent_start, recent_end, sc)
            suspect, reason = _verdict(disc, spike, mort)
            out[pid] = {
                "discount_trend": disc, "sales_spike": spike, "mortality": mort,
                "suspect": suspect, "reason": reason,
            }
        return out
    finally:
        if own_session:
            session.close()


def _median_discount(session, pid, start, end, sc) -> Optional[float]:
    """窗口内该款各笔成交的折扣%中位值 = (original − sale) / original × 100。无数据返 None。"""
    q = (session.query(OrderLineItem.sale_price, OrderLineItem.original_price)
         .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
         .filter(OrderLineItem.product_id == pid,
                 OrderHeader.paid_time.isnot(None),
                 OrderHeader.paid_time >= start,
                 OrderHeader.paid_time <= end,
                 OrderLineItem.original_price.isnot(None),
                 OrderLineItem.original_price > 0))
    q = _scope_filters(q, OrderHeader, **sc)
    discs = [(float(o) - float(sp)) / float(o) * 100 for sp, o in q.all() if o]
    return median(discs) if discs else None


def _count_units(session, pid, start, end, sc) -> int:
    """窗口内该款已付款 line_item 条数（=件数，TikTok 每件一条 line_item）。"""
    q = (session.query(func.count(OrderLineItem.line_item_id))
         .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
         .filter(OrderLineItem.product_id == pid,
                 OrderHeader.paid_time.isnot(None),
                 OrderHeader.paid_time >= start,
                 OrderHeader.paid_time <= end))
    q = _scope_filters(q, OrderHeader, **sc)
    return int(q.scalar() or 0)


def _discount_trend(session, pid, prior_start, prior_end, recent_start, recent_end, sc) -> dict:
    early = _median_discount(session, pid, prior_start, prior_end, sc)
    recent = _median_discount(session, pid, recent_start, recent_end, sc)
    if early is None or recent is None:
        return {"early_pct": _r(early), "recent_pct": _r(recent), "delta_pp": None, "deepening": False}
    delta = round(recent - early, 1)
    return {"early_pct": _r(early), "recent_pct": _r(recent), "delta_pp": delta,
            "deepening": delta >= DEEPENING_PP}


def _sales_spike(session, pid, prior_start, prior_end, recent_start, recent_end,
                 recent_days, prior_days, sc) -> dict:
    recent_daily = _count_units(session, pid, recent_start, recent_end, sc) / recent_days
    prior_daily = _count_units(session, pid, prior_start, prior_end, sc) / prior_days
    if prior_daily <= 0:
        return {"recent_daily": round(recent_daily, 2), "prior_daily": 0.0,
                "ratio": None, "spiking": False}
    ratio = recent_daily / prior_daily
    return {"recent_daily": round(recent_daily, 2), "prior_daily": round(prior_daily, 2),
            "ratio": round(ratio, 2), "spiking": ratio >= SPIKE_RATIO}


def _mortality(session, pid, recent_start, recent_end, sc) -> dict:
    """该款SKU无销率：窗口内有销量的SKU占比的补集。"""
    sku_ids = [r[0] for r in session.query(SkuVariant.sku_id).filter(SkuVariant.product_id == pid).all()]
    total = len(sku_ids)
    if total == 0:
        return {"total": 0, "selling": 0, "dead_rate": None, "high": False}
    q = (session.query(func.count(func.distinct(OrderLineItem.sku_id)))
         .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
         .filter(OrderLineItem.product_id == pid,
                 OrderLineItem.sku_id.in_(sku_ids),
                 OrderHeader.paid_time.isnot(None),
                 OrderHeader.paid_time >= recent_start,
                 OrderHeader.paid_time <= recent_end))
    q = _scope_filters(q, OrderHeader, **sc)
    selling = int(q.scalar() or 0)
    dead_rate = (total - selling) / total
    return {"total": total, "selling": selling, "dead_rate": round(dead_rate, 2),
            "high": dead_rate >= MORTALITY_HIGH}


def _verdict(disc: dict, spike: dict, mort: dict) -> tuple[bool, str]:
    """综合判：折扣加深（主）或「突刺+高无销率」（清尾货型）→ 嫌疑。reason 汇总命中项。"""
    parts = []
    if disc["deepening"]:
        parts.append(f"折扣加深 {disc['delta_pp']:+.0f}pp（{disc['early_pct']:.0f}%→{disc['recent_pct']:.0f}%）")
    if spike.get("spiking"):
        parts.append(f"销量突刺（近期日均{spike['recent_daily']} vs 早期{spike['prior_daily']}）")
    if mort.get("high") and mort.get("dead_rate") is not None:
        parts.append(f"{mort['total']}个SKU仅{mort['selling']}个在销（{mort['dead_rate']*100:.0f}%无销量）")
    suspect = bool(disc["deepening"]) or (bool(spike.get("spiking")) and bool(mort.get("high")))
    return suspect, "；".join(parts)


def _r(v: Optional[float]) -> Optional[int]:
    """折扣%取整（None 透传）。"""
    return None if v is None else round(v)
