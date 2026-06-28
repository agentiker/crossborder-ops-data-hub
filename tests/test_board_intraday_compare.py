"""看板「窗口含今日」环比 intraday 公平比较回归锁。

看板默认窗口结束在今天(WIB)。若环比按「当期整窗 vs 上期整窗」算,当期被半天今天拉低→
显示假暴跌,客户误以为今天掉了。修法:含今日时 cur/prev 都用 get_gmv_summary_intraday_range
钉「截至此刻」(与日报同款),并下发 window.includes_today + as_of_label 供前端徽章/利润卡提示。

此处锁定 web.routes.board._overview_window_and_gmv 的分支选择与 window 元信息。
"""
from datetime import date, datetime, time

import pytest

from web.routes import board as board_mod


@pytest.fixture
def _record_calls(monkeypatch):
    """记录走了 full-day 还是 intraday-range 取数,返回 (calls, set_now)。"""
    calls = []

    def fake_full(**kw):
        calls.append(("full", kw["start_date"], kw["end_date"]))
        return {"gmv": 100.0, "order_count": 10, "units_sold": 12, "avg_order_value": 10.0}

    def fake_intraday(**kw):
        calls.append(("intraday", kw["start_date"], kw["end_date"], kw["cutoff"]))
        return {"gmv": 50.0, "order_count": 5, "units_sold": 6, "avg_order_value": 10.0}

    monkeypatch.setattr(board_mod, "get_gmv_summary", fake_full)
    monkeypatch.setattr(board_mod, "get_gmv_summary_intraday_range", fake_intraday)
    monkeypatch.setattr(board_mod, "business_now",
                        lambda: datetime(2026, 6, 28, 14, 30))
    return calls


def test_window_ending_today_uses_intraday(_record_calls, monkeypatch):
    monkeypatch.setattr(board_mod, "business_today", lambda: date(2026, 6, 28))
    cur, prev, window = board_mod._overview_window_and_gmv(
        date(2026, 6, 22), date(2026, 6, 28),   # 当期结束=今天
        date(2026, 6, 15), date(2026, 6, 21),
        platform="tiktok_shop", country=None, shop_ids=None,
    )
    # 两次取数都走 intraday-range,且 cutoff=此刻 14:30
    kinds = [c[0] for c in _record_calls]
    assert kinds == ["intraday", "intraday"]
    assert all(c[3] == time(14, 30) for c in _record_calls)
    # 窗口元信息
    assert window["includes_today"] is True
    assert window["as_of_label"] and "当日累计" in window["as_of_label"]
    assert window["start"] == "2026-06-22" and window["end"] == "2026-06-28"


def test_window_ending_in_past_uses_full_day(_record_calls, monkeypatch):
    monkeypatch.setattr(board_mod, "business_today", lambda: date(2026, 6, 28))
    cur, prev, window = board_mod._overview_window_and_gmv(
        date(2026, 6, 15), date(2026, 6, 21),   # 当期结束=过去
        date(2026, 6, 8), date(2026, 6, 14),
        platform="tiktok_shop", country=None, shop_ids=None,
    )
    kinds = [c[0] for c in _record_calls]
    assert kinds == ["full", "full"]
    assert window["includes_today"] is False
    assert window["as_of_label"] is None


def test_intraday_avoids_false_drop(monkeypatch):
    """公平比较的意义:含今日时上期也只截到同一时刻,环比不再被半天拖成假跌。

    用真实函数语义建模:full-day 上期=整天(大),intraday 上期=截至此刻(与当期同口径)。
    """
    monkeypatch.setattr(board_mod, "business_today", lambda: date(2026, 6, 28))
    monkeypatch.setattr(board_mod, "business_now", lambda: datetime(2026, 6, 28, 12, 0))

    # 当期今天截至此刻 GMV=60;上期那天整天=120、但截至同一时刻=55
    def fake_full(**kw):
        return {"gmv": 120.0, "order_count": 12, "units_sold": 12, "avg_order_value": 10.0}

    def fake_intraday(**kw):
        # 当期(end=28)截至此刻=60;上期(end=21)截至此刻=55
        g = 60.0 if kw["end_date"] == date(2026, 6, 28) else 55.0
        return {"gmv": g, "order_count": 6, "units_sold": 6, "avg_order_value": 10.0}

    monkeypatch.setattr(board_mod, "get_gmv_summary", fake_full)
    monkeypatch.setattr(board_mod, "get_gmv_summary_intraday_range", fake_intraday)

    cur, prev, _ = board_mod._overview_window_and_gmv(
        date(2026, 6, 28), date(2026, 6, 28),
        date(2026, 6, 21), date(2026, 6, 21),
        platform="tiktok_shop", country=None, shop_ids=None,
    )
    # intraday: 60 vs 55 → 环比 ~+9%(正),而非 full-day 的 60 vs 120 假暴跌 −50%
    change = board_mod._pct(cur["gmv"], prev["gmv"])
    assert change > 0
