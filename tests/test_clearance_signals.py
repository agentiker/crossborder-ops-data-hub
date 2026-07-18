"""clearance_signals 单测：聚焦纯判定逻辑 _verdict（阈值组合）+ 边界。
查询正确性（折扣趋势/无销率/销量脉冲的 SQL）靠 prod 真实数据验证，不在此重复造数据。"""
from __future__ import annotations

from services.clearance_signals import (
    DEEPENING_PP,
    MORTALITY_HIGH,
    SPIKE_RATIO,
    _verdict,
    compute_clearance_signals,
)


def _disc(*, deepening=False, delta_pp=0.0, early=50, recent=50):
    return {"deepening": deepening, "delta_pp": delta_pp, "early_pct": early, "recent_pct": recent}


def _spike(*, spiking=False, recent=1.0, prior=1.0):
    return {"spiking": spiking, "recent_daily": recent, "prior_daily": prior, "ratio": None}


def _mort(*, high=False, dead=0.1, total=10, selling=9):
    return {"high": high, "dead_rate": dead, "total": total, "selling": selling}


def test_no_signals_not_suspect():
    suspect, reason = _verdict(_disc(), _spike(), _mort())
    assert suspect is False
    assert reason == ""


def test_deepening_alone_is_suspect():
    """折扣加深是主信号，单独命中即定罪（即便无销率低/无突刺）。"""
    suspect, reason = _verdict(_disc(deepening=True, delta_pp=10, early=50, recent=60),
                               _spike(), _mort())
    assert suspect is True
    assert "折扣加深 +10pp" in reason
    assert "50%→60%" in reason


def test_spike_plus_mortality_is_suspect():
    """突刺 + 高无销率（清尾货型）双满足才定罪。"""
    suspect, reason = _verdict(_disc(), _spike(spiking=True, recent=6, prior=1),
                               _mort(high=True, dead=0.9, total=20, selling=2))
    assert suspect is True
    assert "销量突刺" in reason and "无销量" in reason


def test_spike_alone_not_suspect():
    """单纯突刺（无高无销率）不定罪——避免把有机增长误判成清仓。"""
    suspect, _ = _verdict(_disc(), _spike(spiking=True, recent=6, prior=1), _mort())
    assert suspect is False


def test_mortality_alone_not_suspect():
    """单纯高无销率（正常长尾款）不定罪。"""
    suspect, _ = _verdict(_disc(), _spike(), _mort(high=True, dead=0.85, total=20, selling=3))
    assert suspect is False


def test_thresholds_exposed():
    """阈值是模块常量，可调（运营按真实数据观感改）。"""
    assert DEEPENING_PP > 0 and SPIKE_RATIO > 1 and 0 < MORTALITY_HIGH < 1


def test_empty_product_ids_returns_empty():
    """空 product_id 列表不查库、直接返 {}。"""
    assert compute_clearance_signals([]) == {}
    assert compute_clearance_signals([None, ""]) == {}
