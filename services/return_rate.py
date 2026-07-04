"""预估退货率取数（阶段3a→3b：真实历史率优先，配置率兜底）。

预估退货 = 退货率 × 当期 GMV（保留「率×GMV」结构，避免真实退货滞后高估当期利润；
真实退货金额滞后发生，不能直接扣当期）。**率本身升级为真实历史率**：取近 N 天真实
退货率（付款后取消额 ÷ GMV，见 refund_metrics），算不出（样本不足/GMV=0）再回落
配置率。真实率稳定（prod 实测 0.4-0.5%，配置默认 5% 高估约 11 倍）→ 真实率显著更准。

取数优先级（率来源）：真实历史率 > [配置] sku > category > default(表) > settings 常数。
真实率护栏：近 N 天 GMV>0 且退款单数 ≥ 最小样本，否则视为不可信、回落配置率。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from core.config import settings
from core.db import SessionLocal
from models.base_models import ReturnRateConfig

logger = logging.getLogger(__name__)

# 真实历史退货率的回看窗口与样本护栏（小样本噪声大 → 回落配置率）。
_REAL_RATE_LOOKBACK_DAYS = 30
_REAL_RATE_MIN_ORDERS = 20  # 近窗退款单数下限；不足则率不可信


def _lookup(session, *, account_id, platform, scope_level, scope_value) -> Optional[Decimal]:
    row = (
        session.query(ReturnRateConfig.return_rate)
        .filter_by(
            account_id=account_id,
            platform=platform,
            scope_level=scope_level,
            scope_value=scope_value,
        )
        .first()
    )
    return Decimal(str(row[0])) if row else None


def get_return_rate(
    *,
    account_id: Optional[str],
    platform: str = "tiktok_shop",
    category: Optional[str] = None,
    sku: Optional[str] = None,
    session=None,
) -> Decimal:
    """按 sku > category > default 表 > settings 常数 的优先级返回预估退货率（小数，0.05=5%）。"""
    own = session is None
    session = session or SessionLocal()
    try:
        if sku:
            r = _lookup(session, account_id=account_id, platform=platform,
                        scope_level="sku", scope_value=sku)
            if r is not None:
                return r
        if category:
            r = _lookup(session, account_id=account_id, platform=platform,
                        scope_level="category", scope_value=category)
            if r is not None:
                return r
        r = _lookup(session, account_id=account_id, platform=platform,
                    scope_level="default", scope_value="")
        if r is not None:
            return r
        return Decimal(str(settings.estimated_return_rate_default))
    finally:
        if own:
            session.close()


def _real_historical_rate(
    *,
    platform: str,
    country: Optional[str],
    shop_id: Optional[str],
    as_of: date,
) -> Optional[Decimal]:
    """近 _REAL_RATE_LOOKBACK_DAYS 天真实退货率（付款后取消额 ÷ 展示 GMV），不可信返回 None。

    不可信 = 窗口 GMV≤0 或退款单数 < 最小样本（小样本率噪声大）。任何异常吞掉回 None
    → 上层回落配置率，绝不让真实率取数失败阻断利润聚合。窗口以 as_of（业务日）向前推。
    口径注：分子分母都是「展示口径」（sub_total/排除取消），与利润卡乘的利润口径 GMV
    略有含运费税差异，但退货率是比例近似、此漂移远小于配置率 5% vs 真实 0.5% 的偏差。
    """
    try:
        from services.refund_metrics import get_refund_summary

        start = as_of - timedelta(days=_REAL_RATE_LOOKBACK_DAYS)
        summary = get_refund_summary(
            start_date=start, end_date=as_of,
            platform=platform, country=country, shop_id=shop_id,
        )
        rate = summary.get("refund_rate")
        orders = summary.get("refund_order_count") or 0
        gmv = summary.get("gmv") or 0
        if rate is None or gmv <= 0 or orders < _REAL_RATE_MIN_ORDERS:
            return None
        return Decimal(str(rate))
    except Exception:  # noqa: BLE001 — fail-safe：真实率算不出一律回落配置率
        logger.warning("real return rate lookup failed shop=%s", shop_id, exc_info=True)
        return None


def get_effective_return_rate(
    *,
    account_id: Optional[str],
    platform: str = "tiktok_shop",
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    as_of: Optional[date] = None,
    session=None,
) -> Decimal:
    """生效退货率：真实历史率优先，算不出回落配置率（get_return_rate 链）。

    利润聚合按 shop 逐日调用；as_of 传当期 metric_date，真实率取该店近 30 天窗口。
    真实率不可信（样本不足/无 GMV）→ 回落 sku>category>default>settings 配置链。
    """
    if as_of is None:
        as_of = date.today()
    real = _real_historical_rate(
        platform=platform, country=country, shop_id=shop_id, as_of=as_of,
    )
    if real is not None:
        return real
    return get_return_rate(
        account_id=account_id, platform=platform, session=session,
    )

