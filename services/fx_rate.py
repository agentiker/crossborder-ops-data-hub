"""汇率换算 service（IDR→CNY，查中国银行外汇牌价日表，回落固定配置值）。

利润统一折 CNY 展示：GMV/扣点/广告/退货（IDR）× idr_to_rmb 折算；产品成本本就是 RMB 不折。
优先查 fact_exchange_rate（flows/sync_exchange_rate 每日抓中行牌价入库），按业务日取 IDR 中行
折算价 / unit；查不到（历史无牌价/表空）回落 settings.idr_to_rmb 固定值，fail-safe 不中断利润。
对外签名 get_idr_to_rmb(on_date) / convert_idr_to_rmb 不变，profit_aggregation 无感切换。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from core.config import settings

# 进程内缓存：按 on_date 缓存命中的汇率（同日多次折算不重复查库；profit_aggregation 每次
# 聚合对同一 metric_date 折算 4 次）。只缓存命中的 Decimal，不缓存 None——避免「汇率尚未抓到
# 时查了一次空」粘住、导致当天始终回落固定值。次日 key 变自然刷新。
_RATE_CACHE: dict[date, Decimal] = {}


def clear_rate_cache() -> None:
    """清空进程内汇率缓存（测试用；生产按日 key 自然失效，无需主动清）。"""
    _RATE_CACHE.clear()


def _fetch_boc_rate(on_date: date) -> Optional[Decimal]:
    """查中行牌价日表取 on_date 当日（或之前最近交易日）IDR→CNY 折算率的**当日均值**。

    一天多样本（日内多次抓取，见 exchange_rate_store）：对该业务日所有 IDR 样本的
    rate_middle/unit 取简单平均（采样时刻均值，非成交量加权——无量数据；日粒度口径足够）。
    当日无样本 → 回退到 ≤on_date 最近一个有牌价的交易日，取那天的均值（周末/节假日兜底）。
    表内无任何 ≤on_date 的 IDR 行 → 返回 None（回落固定值）。
    FactExchangeRate 无 account_id 列 → 不受 core/db 多租户过滤影响，全局可查。
    任何查库异常（连不上/表未建）都吞掉回落 None，绝不让汇率查表中断利润计算。
    """
    try:
        from sqlalchemy import func

        from core.db import SessionLocal
        from models.base_models import FactExchangeRate

        session = SessionLocal()
        try:
            base = session.query(FactExchangeRate).filter(
                FactExchangeRate.currency_code == "IDR",
                FactExchangeRate.metric_date <= on_date,
            )
            # 定位当日或之前最近一个有牌价的业务日
            target_date = (
                base.with_entities(func.max(FactExchangeRate.metric_date)).scalar()
            )
            if target_date is None:
                return None
            # 取该业务日所有样本，用 Decimal 在 Python 里精确平均（不依赖 SQL AVG 的返回类型——
            # SQLite 返 float 有浮点尾差，MySQL 返 Decimal；样本一天最多 3 条，量极小）。
            rows = (
                session.query(FactExchangeRate.rate_middle, FactExchangeRate.unit)
                .filter(
                    FactExchangeRate.currency_code == "IDR",
                    FactExchangeRate.metric_date == target_date,
                )
                .all()
            )
            per_idr = []
            for rate_middle, unit in rows:
                if rate_middle is None:
                    continue
                u = Decimal(str(unit)) if unit else Decimal("1")
                if u == 0:
                    u = Decimal("1")
                per_idr.append(Decimal(str(rate_middle)) / u)
            if not per_idr:
                return None
            return sum(per_idr) / Decimal(len(per_idr))
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — fail-safe：查表失败一律回落固定值
        return None


def get_idr_to_rmb(on_date: Optional[date] = None) -> Decimal:
    """返回 IDR→CNY 乘数。优先查中行牌价日表（按业务日），查不到回落 settings.idr_to_rmb。"""
    if on_date is not None:
        cached = _RATE_CACHE.get(on_date)
        if cached is not None:
            return cached
        rate = _fetch_boc_rate(on_date)
        if rate is not None:
            _RATE_CACHE[on_date] = rate
            return rate
    return Decimal(str(settings.idr_to_rmb))


def convert_idr_to_rmb(amount_idr: Decimal, on_date: Optional[date] = None) -> Decimal:
    """把 IDR 金额折算成 CNY。amount_idr 容错（None→0）。"""
    if amount_idr is None:
        return Decimal("0")
    if not isinstance(amount_idr, Decimal):
        amount_idr = Decimal(str(amount_idr))
    return amount_idr * get_idr_to_rmb(on_date)
