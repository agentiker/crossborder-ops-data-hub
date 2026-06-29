"""业务归日时区工具（单一事实源）。

订单 `paid_time` 存 naive UTC，但 GMV/订单/趋势的"哪天"应按**店铺当地自然日**算
（印尼 WIB = UTC+7，无夏令时）——否则 UTC 17:00~23:59 的单会被算到前一天，
边界日（今天、本周第一天）的数字全错。

偏移由 `settings.business_tz_offset_hours` 配置（本期固定 7）。归日一律在此模块完成，
data API 与 order_metrics 共用，避免散落各处口径漂移；也不在 SQL 里做 `date(col+interval)`
（SQLite/MySQL 方言不同），改在 Python 端按 `to_business_day` 归日，规避方言风险。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from core.config import settings

# 业务时区相对 UTC 的偏移（印尼 WIB = +7）
OFFSET = timedelta(hours=settings.business_tz_offset_hours)


def _utcnow_naive() -> datetime:
    """当前 UTC 时间（naive，与 paid_time 的存储口径一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def business_today() -> date:
    """业务时区（印尼）的"今天"。用于默认日期窗口，替代 `date.today()`（服务器本地时区）。"""
    return (_utcnow_naive() + OFFSET).date()


def to_business_day(dt: datetime) -> date:
    """naive UTC datetime → 业务时区（印尼）的自然日。用于把 paid_time 归日。"""
    return (dt + OFFSET).date()


def to_business_hour(dt: datetime) -> datetime:
    """naive UTC datetime → 业务时区（印尼）的当地小时桶（naive，分秒归零）。

    与 to_business_day 同源 OFFSET，用于把 paid_time 按印尼当地小时归桶（单天逐小时趋势）。
    例：UTC 6/28 23:57 → 印尼 6/29 06:57 → 桶 datetime(2026, 6, 29, 6, 0, 0)。
    """
    return (dt + OFFSET).replace(minute=0, second=0, microsecond=0)


def business_now() -> datetime:
    """业务时区（印尼）的"此刻"（naive）。用于当日报告的"截至现在"时点。"""
    return _utcnow_naive() + OFFSET


def business_hour_now() -> datetime:
    """业务时区（印尼）当前小时桶（naive，分秒归零）。

    用于"今天逐小时趋势只画到当前小时"的上界截断：印尼 13:42 → datetime(.., 13, 0, 0)。
    """
    return business_now().replace(minute=0, second=0, microsecond=0)


def intraday_window_utc(day: date, cutoff: time) -> tuple[datetime, datetime]:
    """业务日 day 从 00:00 到 cutoff 时刻 → naive UTC 查询边界。

    用于"当日截至此刻 / 昨日截至同一时刻"的同期对比（与 paid_window_utc 同口径，仅上界换成时点）。
    """
    start_dt = datetime.combine(day, time.min) - OFFSET
    end_dt = datetime.combine(day, cutoff) - OFFSET
    return start_dt, end_dt


def paid_window_utc(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    """业务日闭区间 [start_date, end_date] → 对应的 naive UTC 查询边界 [start_dt, end_dt]。

    印尼 D 00:00:00 = UTC (D 00:00:00 − OFFSET)；印尼 D 23:59:59.999999 = UTC (… − OFFSET)。
    例：印尼 6/9 → UTC [6/8 17:00:00, 6/9 16:59:59.999999]，那笔 UTC 6/8 23:57 的单正确落入 6/9。
    """
    start_dt = datetime.combine(start_date, time.min) - OFFSET
    end_dt = datetime.combine(end_date, time.max) - OFFSET
    return start_dt, end_dt


# 相对时间词 → 业务日窗口（按印尼今天 + 周一起算）。把"本周/今天"的换算从 LLM 手里收回服务端，
# 避免弱模型算错星期、强弱模型周起算习惯不一致。
PERIOD_KEYS = (
    "today", "yesterday", "this_week", "last_week", "last_7d", "last_30d", "this_month",
)


WEEKDAYS_ZH = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _fmt_day(d: date) -> str:
    """`6/9（周二）`——月/日 + 中文星期。星期由服务端按印尼业务日算，避免 LLM 推错。"""
    return f"{d.month}/{d.day}（{WEEKDAYS_ZH[d.weekday()]}）"


def describe_window(start_date: date, end_date: date) -> str:
    """业务日窗口 → 人读权威描述（含星期、天数、是否含今天）。供响应 `window_label` 字段下发。

    弱模型不可靠地知道"今天几号/某天周几"（曾把周二答成周一）。和 resolve_period 同理，把
    星期/今天的判断收回服务端：agent 复述本串即可，**不要自己推算星期或今天是周几**。
    串首固定带"印尼时间"前缀，让 agent 复述首行时即向客户说明时区基准（北京 0 点后印尼仍是前一天）。
    例：本周 6/8~6/9（今天 6/9）→ `印尼时间 6/8（周一） ~ 6/9（周二），共 2 天；今天 6/9（周二）`；
    昨天 6/8 → `印尼时间 6/8（周一）`；今天 6/9 → `印尼时间 6/9（周二，今天）`。
    """
    today = business_today()
    if start_date == end_date:
        d = start_date
        suffix = "，今天" if d == today else ""
        body = f"{d.month}/{d.day}（{WEEKDAYS_ZH[d.weekday()]}{suffix}）"
    else:
        days = (end_date - start_date).days + 1
        body = f"{_fmt_day(start_date)} ~ {_fmt_day(end_date)}，共 {days} 天"
        if start_date <= today <= end_date:
            body += f"；今天 {_fmt_day(today)}"
    return f"印尼时间 {body}"


def resolve_period(period: str) -> tuple[date, date]:
    """相对时间词 → 业务日闭区间 [start_date, end_date]（印尼时区，周一为一周起点）。

    today/yesterday：单日；this_week：本周一~今天；last_week：上周一~上周日；
    last_7d/last_30d：含今天往前 N 天；this_month：本月 1 号~今天。未知值抛 ValueError。
    """
    t = business_today()
    if period == "today":
        return t, t
    if period == "yesterday":
        y = t - timedelta(days=1)
        return y, y
    if period == "this_week":
        monday = t - timedelta(days=t.weekday())  # weekday(): Mon=0
        return monday, t
    if period == "last_week":
        this_monday = t - timedelta(days=t.weekday())
        return this_monday - timedelta(days=7), this_monday - timedelta(days=1)
    if period == "last_7d":
        return t - timedelta(days=6), t
    if period == "last_30d":
        return t - timedelta(days=29), t
    if period == "this_month":
        return t.replace(day=1), t
    raise ValueError(f"未知 period：{period!r}，可选 {', '.join(PERIOD_KEYS)}")


def previous_window(start_date: date, end_date: date) -> tuple[date, date]:
    """紧邻当期、等长的上一窗口（环比基准）。

    含端点天数 N 的窗口 → 上一窗口为其前 N 天的闭区间。
    例：近 30 天 [6/13, 7/12]（含端点 30 天）→ 上一窗口 [5/14, 6/12]。
    口径中性、对所有 period 通用，不臆造自然周/月对齐（故前端文案统一「vs 上期」）。
    """
    length = (end_date - start_date).days + 1
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return prev_start, prev_end
