"""相对时间词 → 业务日窗口 的下沉逻辑（resolve_period / _resolve_window）。

把"今天/本周/近7天"的换算从 LLM 手里收回服务端，避免弱模型算错星期。
基准印尼今天固定为 2026-06-09（周二，weekday=1），便于断言周/月边界。
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

import core.timezone as tz
import web.routes.data as data
from core.timezone import PERIOD_KEYS, describe_window, resolve_period

REF = date(2026, 6, 9)  # 周二


@pytest.fixture()
def fixed_today(monkeypatch):
    """把业务今天钉死在 2026-06-09。resolve_period 走 core.timezone.business_today，
    _resolve_window 走 web.routes.data.business_today，两处都要替。"""
    monkeypatch.setattr(tz, "business_today", lambda: REF)
    monkeypatch.setattr(data, "business_today", lambda: REF)


@pytest.mark.parametrize(
    "period, expected",
    [
        ("today", (date(2026, 6, 9), date(2026, 6, 9))),
        ("yesterday", (date(2026, 6, 8), date(2026, 6, 8))),
        ("this_week", (date(2026, 6, 8), date(2026, 6, 9))),   # 周一(6/8)~今天
        ("last_week", (date(2026, 6, 1), date(2026, 6, 7))),   # 上周一~上周日
        ("last_7d", (date(2026, 6, 3), date(2026, 6, 9))),     # 含今天往前7天
        ("last_30d", (date(2026, 5, 11), date(2026, 6, 9))),   # 含今天往前30天
        ("this_month", (date(2026, 6, 1), date(2026, 6, 9))),  # 本月1号~今天
    ],
)
def test_resolve_period_windows(fixed_today, period, expected):
    assert resolve_period(period) == expected


def test_resolve_period_keys_all_covered():
    """PERIOD_KEYS 里每个词都能被 resolve_period 解析（防新增 key 漏实现）。"""
    for p in PERIOD_KEYS:
        sd, ed = resolve_period(p)
        assert sd <= ed


def test_resolve_period_unknown_raises():
    with pytest.raises(ValueError):
        resolve_period("last_quarter")


def test_resolve_window_explicit_beats_period(fixed_today):
    """显式 start/end 优先于 period：传了日期就忽略 period。"""
    sd, ed = data._resolve_window("2026-01-01", "2026-01-31", "today", default_back_days=7)
    assert (sd, ed) == (date(2026, 1, 1), date(2026, 1, 31))


def test_resolve_window_partial_explicit_fills_default(fixed_today):
    """只给 start_date：end_date 补业务今天，default_back_days 不参与。"""
    sd, ed = data._resolve_window("2026-06-01", None, None, default_back_days=7)
    assert (sd, ed) == (date(2026, 6, 1), REF)


def test_resolve_window_period_when_no_explicit(fixed_today):
    sd, ed = data._resolve_window(None, None, "this_week", default_back_days=7)
    assert (sd, ed) == (date(2026, 6, 8), REF)


def test_resolve_window_default_window(fixed_today):
    """既无显式日期也无 period → 近 default_back_days 天。"""
    sd, ed = data._resolve_window(None, None, None, default_back_days=6)
    assert (sd, ed) == (date(2026, 6, 3), REF)


def test_resolve_window_bad_period_raises_400(fixed_today):
    with pytest.raises(HTTPException) as exc:
        data._resolve_window(None, None, "last_quarter", default_back_days=7)
    assert exc.value.status_code == 400


# describe_window：权威星期/今天 描述（弱模型不能自己推算，曾把周二答成周一）。
# REF=2026-06-09 是周二（weekday=1）。

def test_describe_window_this_week_includes_today(fixed_today):
    # 本周 6/8(周一)~6/9(周二)，含今天 6/9
    label = describe_window(date(2026, 6, 8), date(2026, 6, 9))
    assert label == "印尼时间 6/8（周一） ~ 6/9（周二），共 2 天；今天 6/9（周二）"


def test_describe_window_single_day_today(fixed_today):
    # 今天 6/9 周二，单日且就是今天 → 带"今天"
    assert describe_window(REF, REF) == "印尼时间 6/9（周二，今天）"


def test_describe_window_single_day_not_today(fixed_today):
    # 昨天 6/8 周一，单日非今天 → 不带"今天"
    assert describe_window(date(2026, 6, 8), date(2026, 6, 8)) == "印尼时间 6/8（周一）"


def test_describe_window_past_range_excludes_today(fixed_today):
    # 上周 6/1(周一)~6/7(周日)，今天 6/9 不在区间 → 不追加"今天"
    label = describe_window(date(2026, 6, 1), date(2026, 6, 7))
    assert label == "印尼时间 6/1（周一） ~ 6/7（周日），共 7 天"
    assert "今天" not in label


def test_describe_window_weekday_is_correct_not_hallucinated(fixed_today):
    # 回归：6/9 是周二（不是周一）——这正是 agent 之前编错的点
    assert "6/9（周二" in describe_window(REF, REF)
