"""汇率走势序列查询（供 /board/fx 页面用）：读 fact_exchange_rate 出日粒度序列 + 可选币种。

与 services/fx_rate 同一张表、同一「日内多样本取当日均值」口径（见 fx_rate._fetch_boc_rate），
但用途不同：fx_rate 是利润折算取单日乘数（只 IDR、带固定值 fail-safe），本模块是给前端画走势，
按币种批量出「metric_date → 当日均值」的日序列，纯只读、不折算、不回落固定值（无数据即空）。

FactExchangeRate 无 account_id 列 → 全局可查，不受 core/db 多租户过滤影响（汇率非隔离数据）。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from core.db import SessionLocal
from models.base_models import FactExchangeRate
from services.exchange_rate_store import _NAME_TO_ISO

logger = logging.getLogger(__name__)

# 常用币种（下拉默认集）：IDR 主战场置顶，其余为跨境常打交道的结算/参考币种。
# 全 40 币种都在库，这里只挑常用几个进下拉，避免选项冗长（见 shape brief 决策）。
COMMON_CURRENCIES = ["IDR", "USD", "CNY", "MYR", "SGD", "THB", "VND", "PHP", "EUR", "HKD"]

# ISO 码 → 中行中文名（反查 _NAME_TO_ISO，供下拉展示「印尼卢比 IDR」）。CNY 表里没有
# （中行牌价是"外币→CNY"，本币不报价）→ 手工补一个中文名，前端显示友好。
_ISO_TO_NAME = {iso: name for name, iso in _NAME_TO_ISO.items()}
_ISO_TO_NAME.setdefault("CNY", "人民币")


def list_currencies() -> list[dict]:
    """下拉选项：常用币种中在库有数据的那些（IDR 恒在），每项 {code, name}。

    只回「库里真有牌价」的币种，避免下拉里选了却空图。CNY 是本币无牌价、但作为对照单位保留。
    查库异常 → 至少回 IDR，保证页面能开。
    """
    try:
        session = SessionLocal()
        try:
            present = {
                code
                for (code,) in session.query(FactExchangeRate.currency_code)
                .distinct()
                .all()
            }
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — fail-safe：查库失败至少给 IDR
        logger.warning("list fx currencies failed", exc_info=True)
        present = {"IDR"}
    codes = [c for c in COMMON_CURRENCIES if c in present or c == "CNY"]
    if "IDR" not in codes:
        codes.insert(0, "IDR")
    return [{"code": c, "name": _ISO_TO_NAME.get(c, c)} for c in codes]


def get_fx_series(currency: str, days: int) -> dict:
    """按币种取近 days 天的日粒度中行折算价序列（当日多样本取均值）。

    返回 {currency, name, unit, points:[{date, rate}], latest, start_rate, change_pct}：
    - rate = 该业务日所有样本 rate_middle/unit 的简单均值（1 外币 → CNY），口径同 fx_rate。
    - points 按日期升序；无数据日不补点（周末/节假日中行不发布，自然断点，前端连线跳过）。
    - latest = 最后一个有数据日的 rate；start_rate = 第一个；change_pct = 区间涨跌幅（%）。
    货币码非法/无数据 → points 空、latest=None，前端走空态。
    """
    currency = (currency or "IDR").upper()
    if days <= 0:
        days = 90
    end = date.today()
    start = end - timedelta(days=days)
    try:
        session = SessionLocal()
        try:
            rows = (
                session.query(
                    FactExchangeRate.metric_date,
                    FactExchangeRate.rate_middle,
                    FactExchangeRate.unit,
                )
                .filter(
                    FactExchangeRate.currency_code == currency,
                    FactExchangeRate.metric_date >= start,
                    FactExchangeRate.metric_date <= end,
                )
                .all()
            )
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — 只读走势，查库失败回空序列不抛 500
        logger.warning("fx series query failed currency=%s", currency, exc_info=True)
        rows = []

    # 按业务日归组，对当日所有样本取 rate_middle/unit 的简单均值（口径同 fx_rate._fetch_boc_rate）。
    by_day: dict[date, list[Decimal]] = {}
    for metric_date, rate_middle, unit in rows:
        if metric_date is None or rate_middle is None:
            continue
        u = Decimal(str(unit)) if unit else Decimal("1")
        if u == 0:
            u = Decimal("1")
        by_day.setdefault(metric_date, []).append(Decimal(str(rate_middle)) / u)

    points = []
    for d in sorted(by_day):
        vals = by_day[d]
        avg = sum(vals) / Decimal(len(vals))
        points.append({"date": d.isoformat(), "rate": float(avg)})

    latest = points[-1]["rate"] if points else None
    start_rate = points[0]["rate"] if points else None
    change_pct = (
        round((latest - start_rate) / start_rate * 100, 2)
        if latest is not None and start_rate not in (None, 0)
        else None
    )
    return {
        "currency": currency,
        "name": _ISO_TO_NAME.get(currency, currency),
        "unit": 1,  # 序列已折成「1 外币 → CNY」，前端纵轴缩放自行处理
        "points": points,
        "latest": latest,
        "start_rate": start_rate,
        "change_pct": change_pct,
    }
