"""待发货超时告警判定（build_decision）+ 静默时段（is_quiet_now）的纯逻辑单测。

均为纯函数：build_decision 吃 metrics dict、is_quiet_now 吃 time，无需 DB/session。
"""
from datetime import time

from services.fulfillment_alerts import build_decision


def _metrics(*, overdue, critical=0, by_shop=None, snapshot="2026-06-13T14:30:00"):
    total = overdue + critical
    return {
        "buckets": {
            "overdue": overdue,
            "critical": critical,
            "normal": 0,
            "unknown": 0,
            "total": total,
        },
        "by_shop": by_shop or [],
        "snapshot_at": snapshot,
    }


def test_zero_to_nonzero_alerts():
    d = build_decision(metrics=_metrics(overdue=3), scope_display="印尼全部店", prev_reported=0)
    assert d.should_alert is True
    assert d.reset_state is False
    assert d.new_reported_overdue == 3
    # 首次（prev=0）只报数，不带「较上次」
    assert "较上次" not in d.message
    assert "已超时：3 单" in d.message


def test_increase_alerts_with_delta():
    d = build_decision(metrics=_metrics(overdue=5), scope_display="印尼全部店", prev_reported=3)
    assert d.should_alert is True
    assert d.delta == 2
    assert "已超时：5 单（较上次 +2）" in d.message
    assert d.new_reported_overdue == 5


def test_flat_does_not_alert():
    d = build_decision(metrics=_metrics(overdue=5), scope_display="x", prev_reported=5)
    assert d.should_alert is False
    assert d.reset_state is False
    assert d.new_reported_overdue == 5  # 游标不动
    assert d.message is None


def test_decrease_does_not_alert():
    d = build_decision(metrics=_metrics(overdue=2), scope_display="x", prev_reported=5)
    assert d.should_alert is False
    assert d.reset_state is False
    assert d.new_reported_overdue == 5  # 不下调，避免反复触发


def test_cleared_requests_reset():
    d = build_decision(metrics=_metrics(overdue=0, critical=2), scope_display="x", prev_reported=5)
    assert d.should_alert is False
    assert d.reset_state is True
    assert d.new_reported_overdue == 0
    assert d.message is None


def test_message_contains_sections_and_top_shops():
    by_shop = [
        {"shop_id": "shopA", "overdue": 3, "critical": 0, "normal": 0, "unknown": 0, "total": 3},
        {"shop_id": "shopB", "overdue": 1, "critical": 0, "normal": 0, "unknown": 0, "total": 1},
        {"shop_id": "shopC", "overdue": 0, "critical": 0, "normal": 1, "unknown": 0, "total": 1},
    ]
    d = build_decision(
        metrics=_metrics(overdue=4, critical=2, by_shop=by_shop),
        scope_display="TikTok Shop / 印尼 / 2 个店铺",
        prev_reported=0,
    )
    msg = d.message
    assert "🚨 待发货超时预警" in msg
    assert "TikTok Shop / 印尼 / 2 个店铺" in msg
    assert "6/13 14:30（印尼时间）" in msg
    assert "临界（24h 内截止）：2 单" in msg
    # 按超时降序，shopC（overdue=0）不出现
    assert "shopA 3单" in msg and "shopB 1单" in msg
    assert "shopC" not in msg
    # 无 Markdown 表格
    assert "|" not in msg


def test_message_handles_missing_snapshot():
    d = build_decision(metrics=_metrics(overdue=1, snapshot=None), scope_display="x", prev_reported=0)
    assert "暂无快照" in d.message


def test_is_quiet_now_cross_midnight():
    from services.fulfillment_alerts import ALERT_TYPE  # noqa: F401  (确认模块可导入)
    from flows.scan_fulfillment_alerts import is_quiet_now

    # 默认静默 23:00~次日 08:30
    assert is_quiet_now(time(23, 30)) is True
    assert is_quiet_now(time(2, 0)) is True
    assert is_quiet_now(time(8, 0)) is True
    assert is_quiet_now(time(8, 30)) is False  # 端点 end 不含
    assert is_quiet_now(time(9, 0)) is False
    assert is_quiet_now(time(14, 0)) is False
