"""汇率换算 service（阶段3a：IDR→CNY 固定配置值，预留易宝 API）。

利润统一折 CNY 展示：GMV/扣点/广告/退货（IDR）× idr_to_rmb 折算；产品成本本就是 RMB 不折。
MVP 用 settings.idr_to_rmb 固定值；3b 接易宝汇率 API（每日历史汇率，按订单支付时间取）替换
_fetch_yeepay_rate 桩，对外签名 get_idr_to_rmb(on_date) 不变。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from core.config import settings


def _fetch_yeepay_rate(on_date: date) -> Optional[Decimal]:
    """（3b 实装）按日取易宝 IDR→CNY 汇率，含进程内缓存。MVP 返回 None → 回落固定配置值。"""
    return None


def get_idr_to_rmb(on_date: Optional[date] = None) -> Decimal:
    """返回 IDR→CNY 乘数。MVP 用 settings.idr_to_rmb 固定值（1 IDR ≈ 0.00045 CNY）。"""
    if on_date is not None:
        rate = _fetch_yeepay_rate(on_date)
        if rate is not None:
            return rate
    return Decimal(str(settings.idr_to_rmb))


def convert_idr_to_rmb(amount_idr: Decimal, on_date: Optional[date] = None) -> Decimal:
    """把 IDR 金额折算成 CNY。amount_idr 容错（None→0）。"""
    if amount_idr is None:
        return Decimal("0")
    if not isinstance(amount_idr, Decimal):
        amount_idr = Decimal(str(amount_idr))
    return amount_idr * get_idr_to_rmb(on_date)
