"""监控告警巡检 flow：确定性判定 + 飞书卡片直投（0 经 LLM）。

覆盖五类告警，每个收件人每轮各自独立判定/去重/投递（互不影响）：
1. 待发货超时（services.fulfillment_metrics + fulfillment_alerts）。
2. 低库存 / 断货（services.stock_metrics + stock_alerts）。
3. 扣点率异常（已结算口径，有结算滞后；services.fee_rate_metrics + fee_rate_alerts）。
4. 及时费率（未结算预估口径，平台调佣结算前即可发现；同上模块 realtime 分支）。
5. 爆单（当日已付款销量破阈；services.order_metrics + hotsell_alerts）。

调度：systemd timer data-scan-alerts（默认每 30 分钟）；本地调试 `python main.py --task alert-scan`。
链路（每条规则）：静默时段跳过 → 取数（确定性分桶）→ build_decision 判定+组装文案 →
      该推则投递（见下）→ 投递成功后写回去重游标。

投递（send_alert）：优先 v2 CardKit 卡片（web/alert_card_builder 拼装 +
web/feishu_card_sender 直投，与日报卡片同链路、方案A深色系配色）；卡片任何失败
回落 openclaw `message send` 纯文本（决不因卡片问题静默丢告警）。
为什么不用 openclaw cron：cron job 必经 agent/LLM，而告警要阈值/去重/稳定文案、每跑必准。
收件人真相源 = alert_recipients 表；空表回落内置常量（见 load_recipients）。
（文件名沿用 scan_fulfillment_alerts 以免动 timer/main 引用；现已是「告警总巡检」。）
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from core.config import settings
from core.db import SessionLocal
from core.timezone import business_today
from services import fee_rate_alerts, hotsell_alerts, stock_alerts
from services.biz_config import get_config_int
from services.fee_rate_metrics import get_settled_fee_rate, get_unsettled_fee_rate
from services.fulfillment_alerts import ALERT_TYPE, build_decision
from services.fulfillment_metrics import get_pending_fulfillments
from services.order_metrics import get_new_product_ids, get_units_by_product
from services.metrics_store import (
    get_fee_rate_alert_state,
    get_fulfillment_alert_state,
    get_hotsell_alert_state,
    get_hotsell_reported_ids,
    get_stock_alert_state,
    get_stock_reported_skus,
    upsert_fee_rate_alert_state,
    upsert_fulfillment_alert_state,
    upsert_hotsell_alert_state,
    upsert_stock_alert_state,
)
from services.stock_metrics import get_stock_risk
from services.scope_resolution import ScopeError

# 收件人**真相源 = alert_recipients 表**（plan/09 Phase 6 迁 DB）。下面常量仅作
# **空表回落**：表未建/未 seed 时退回它，保证迁移过渡期告警不静默中断（会打日志提示）。
# account 对应 ~/.openclaw/openclaw.json 的 channels.feishu.accounts 键；open_id 为飞书用户 ou_xxx。
_FALLBACK_RECIPIENTS = [
    {
        "account": "ecom-app",
        "open_id": "ou_7afe4514b269e5a0abfbd395f3f26410",
        "scope_id": None,
    },
    {
        "account": "ecom-app-gtl",
        "open_id": "ou_5a27000e3e67de797de432a43bac29da",
        "scope_id": None,
    },
]


def load_recipients() -> list[dict]:
    """读 alert_recipients 表的启用收件人；表空/读失败 → 回落常量（打日志）。"""
    try:
        session = SessionLocal()
        try:
            from models.base_models import AlertRecipient

            rows = (
                session.query(AlertRecipient)
                .filter(AlertRecipient.is_active.is_(True))
                .order_by(AlertRecipient.account_id, AlertRecipient.open_id)
                .all()
            )
        finally:
            session.close()
    except Exception as exc:  # 表未建/DB 异常：回落，绝不让告警静默中断
        print(f"[alert] 读 alert_recipients 失败（{exc}），回落内置收件人常量")
        return list(_FALLBACK_RECIPIENTS)

    if not rows:
        print("[alert] alert_recipients 表空，回落内置收件人常量（建议跑 Phase 6 迁移 seed）")
        return list(_FALLBACK_RECIPIENTS)
    return [
        {"account": r.account_id, "open_id": r.open_id, "scope_id": r.scope_key}
        for r in rows
    ]


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def is_quiet_now(now: Optional[time] = None) -> bool:
    """当前是否处于告警静默时段（按 settings.alert_quiet_tz）。

    start <= end：同日窗口 [start, end)；start > end：跨午夜 [start, 次日 end)。
    """
    if now is None:
        now = datetime.now(ZoneInfo(settings.alert_quiet_tz)).time()
    start = _parse_hhmm(settings.alert_quiet_start)
    end = _parse_hhmm(settings.alert_quiet_end)
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def send_feishu_message(
    *, account: str, open_id: str, text: str, dry_run: bool = False
) -> bool:
    """用 openclaw CLI 直投飞书私聊（确定性，不经 agent）。成功返回 True。"""
    cmd = [
        settings.openclaw_bin,
        "message",
        "send",
        "--channel",
        "feishu",
        "--account",
        account,
        "--target",
        f"user:{open_id}",
        "--message",
        text,
    ]
    if dry_run:
        cmd.append("--dry-run")
    # openclaw 是 node CLI，内部会调 `node`；systemd service 的 PATH 不含 nvm 目录，
    # 必须把 openclaw 所在目录（同目录就有 node）加进 PATH，否则 message send 在 service 里失败。
    env = os.environ.copy()
    bin_dir = os.path.dirname(settings.openclaw_bin)
    if bin_dir:
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, env=env
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[alert] message send 调用失败 account={account}: {exc}")
        return False
    if result.returncode != 0:
        print(
            f"[alert] message send 非零退出 account={account} rc={result.returncode}: "
            f"{(result.stderr or result.stdout or '').strip()[:300]}"
        )
        return False
    return True


def send_alert(
    *, account: str, open_id: str, text: str, card: Optional[dict] = None
) -> bool:
    """告警投递：优先发 v2 CardKit 卡片（web/feishu_card_sender 直投，与日报同链路），
    卡片失败回落 openclaw 纯文本——告警绝不能因卡片渲染/凭证问题静默丢失。"""
    if card is not None:
        try:
            from web.feishu_card_sender import send_interactive_card

            send_interactive_card(account, open_id, card)
            return True
        except Exception as exc:  # noqa: BLE001 — 任何卡片失败都回落文本
            print(f"[alert] 卡片投递失败回落文本 account={account}: {exc}")
    return send_feishu_message(account=account, open_id=open_id, text=text)


def _board_url(account: str) -> str:
    """该租户看板公网地址（卡片按钮用）；未配公网域则返回空串（按钮省略）。"""
    try:
        from core.tenancy import public_base_url_for

        base = (public_base_url_for(account) or "").rstrip("/")
        return f"{base}/app/board" if base else ""
    except Exception:  # noqa: BLE001
        return ""


def _scan_fulfillment(session, *, account, open_id, scope, scope_id, dry_run: bool) -> str:
    """待发货超时规则：评估并（必要时）投递。返回一行状态。"""
    metrics = get_pending_fulfillments(
        platform=scope.platform,
        country=scope.country,
        shop_ids=scope.shop_ids or None,
    )
    prev = get_fulfillment_alert_state(
        session, alert_type=ALERT_TYPE, account_id=account, scope_key=scope_id
    )
    prev_reported = prev.last_reported_overdue if prev else 0

    decision = build_decision(
        metrics=metrics,
        scope_display=scope.display_text,
        prev_reported=prev_reported,
    )

    if decision.should_alert:
        if dry_run:
            print(f"[alert][dry-run] 待发货 → {account}/{open_id}:\n{decision.message}")
            return f"{account}/待发货: 待推送 overdue={decision.overdue}（dry-run）"
        from services.shop_directory import get_shop_names
        from web.alert_card_builder import build_fulfillment_card

        card = build_fulfillment_card(
            scope_display=scope.display_text,
            overdue=decision.overdue, critical=decision.critical,
            total=decision.total, delta=decision.delta,
            prev_reported=prev_reported,
            by_shop=list(metrics.get("by_shop") or []),
            shop_names=get_shop_names(account),
            board_url=_board_url(account),
        )
        sent = send_alert(
            account=account, open_id=open_id, text=decision.message, card=card
        )
        if sent:
            upsert_fulfillment_alert_state(
                session,
                alert_type=ALERT_TYPE,
                account_id=account,
                scope_key=scope_id,
                last_reported_overdue=decision.new_reported_overdue,
                last_critical=decision.critical,
                mark_sent=True,
            )
            session.commit()
            return f"{account}/待发货: 已推送 overdue={decision.overdue}(+{decision.delta})"
        return f"{account}/待发货: 推送失败 overdue={decision.overdue}（游标不更新，下轮重试）"

    if decision.reset_state and not dry_run and prev_reported != 0:
        upsert_fulfillment_alert_state(
            session,
            alert_type=ALERT_TYPE,
            account_id=account,
            scope_key=scope_id,
            last_reported_overdue=0,
            last_critical=decision.critical,
        )
        session.commit()
    return f"{account}/待发货: 不推送 overdue={decision.overdue}"


def _scan_stock(session, *, account, open_id, scope, scope_id, dry_run: bool) -> str:
    """低库存/断货规则：评估并（必要时）投递。返回一行状态。"""
    risk = get_stock_risk(
        platform=scope.platform,
        country=scope.country,
        shop_ids=scope.shop_ids or None,
    )
    prev = get_stock_alert_state(
        session, alert_type=stock_alerts.ALERT_TYPE, account_id=account, scope_key=scope_id
    )
    prev_skus = get_stock_reported_skus(prev)

    decision = stock_alerts.build_decision(
        risk=risk,
        scope_display=scope.display_text,
        prev_reported_skus=prev_skus,
    )

    if decision.should_alert:
        if dry_run:
            print(f"[alert][dry-run] 库存 → {account}/{open_id}:\n{decision.message}")
            return f"{account}/库存: 待推送 风险={decision.total} 新增={len(decision.new_skus)}（dry-run）"
        from web.alert_card_builder import build_stock_card

        # 只给卡片风险桶内的 items（与告警口径一致：get_stock_risk 默认就只回风险桶）
        card = build_stock_card(
            scope_display=scope.display_text,
            stockout=decision.stockout, critical=decision.critical,
            warning=decision.warning,
            items=list(risk.get("items") or []),
            new_skus=list(decision.new_skus),
            board_url=_board_url(account),
            critical_days=get_config_int("stock_cover_critical_days", account_id=account),
        )
        sent = send_alert(
            account=account, open_id=open_id, text=decision.message, card=card
        )
        if sent:
            upsert_stock_alert_state(
                session,
                alert_type=stock_alerts.ALERT_TYPE,
                account_id=account,
                scope_key=scope_id,
                reported_skus=decision.new_reported_skus,
                mark_sent=True,
            )
            session.commit()
            return f"{account}/库存: 已推送 风险={decision.total}(新增{len(decision.new_skus)})"
        return f"{account}/库存: 推送失败 风险={decision.total}（游标不更新，下轮重试）"

    # 不推：更新游标（清空 / 收敛到当前集，让恢复的 SKU 移出）。
    if not dry_run and (decision.reset_state or decision.new_reported_skus != sorted(set(prev_skus))):
        upsert_stock_alert_state(
            session,
            alert_type=stock_alerts.ALERT_TYPE,
            account_id=account,
            scope_key=scope_id,
            reported_skus=decision.new_reported_skus,
        )
        session.commit()
    return f"{account}/库存: 不推送 风险={decision.total}"


def _fmt_window(start, end) -> str:
    """业务日窗口 → '6/1~6/7'。"""
    return f"{start.month}/{start.day}~{end.month}/{end.day}"


def _scan_fee_rate(session, *, account, open_id, scope, scope_id, dry_run: bool) -> str:
    """扣点率异常规则：评估窗口费率 vs 基准均值，异常升高则（必要时）投递。返回一行状态。

    结算滞后口径：评估窗口取「今天 − settle_lag」回看一段已结算完的天；基准取其前若干天。
    去重：同一评估窗口结束日只报一次（last_window_end）。
    """
    today = business_today()
    lag = settings.fee_rate_settle_lag_days
    eval_end = today - timedelta(days=lag)
    eval_start = eval_end - timedelta(days=settings.fee_rate_eval_window_days - 1)
    baseline_end = eval_start - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=settings.fee_rate_baseline_days - 1)

    common_scope = dict(
        platform=scope.platform, country=scope.country, shop_ids=scope.shop_ids or None
    )
    eval_by_ccy = get_settled_fee_rate(
        start_date=eval_start, end_date=eval_end, session=session, **common_scope
    )
    baseline_by_ccy = get_settled_fee_rate(
        start_date=baseline_start, end_date=baseline_end, session=session, **common_scope
    )

    decision = fee_rate_alerts.build_decision(
        eval_by_ccy=eval_by_ccy,
        baseline_by_ccy=baseline_by_ccy,
        scope_display=scope.display_text,
        min_gmv=settings.fee_rate_min_gmv,
        rel_pct=settings.fee_rate_alert_rel_pct,
        abs_pct=settings.fee_rate_alert_abs_pct,
        eval_window_label=_fmt_window(eval_start, eval_end),
        baseline_window_label=_fmt_window(baseline_start, baseline_end),
    )

    if not decision.should_alert:
        return f"{account}/扣点率: 不推送（{decision.skip_reason or '无异常'}）"

    # 去重：同一评估窗口已报过则不复读
    prev = get_fee_rate_alert_state(
        session, alert_type=fee_rate_alerts.ALERT_TYPE, account_id=account, scope_key=scope_id
    )
    if prev is not None and prev.last_window_end is not None and prev.last_window_end >= eval_end:
        return f"{account}/扣点率: 不推送（窗口 {eval_end} 已报过）"

    from services.tiktok_policy_evidence import get_policy_references

    policy_refs = get_policy_references(
        country=scope.country,
        fee_keys=fee_rate_alerts.evidence_fee_keys(decision.evidence),
        alert_date=eval_end,
    )
    text = fee_rate_alerts.enrich_message_with_evidence(
        decision.message or "",
        policy_references=policy_refs,
    )

    if dry_run:
        print(f"[alert][dry-run] 扣点率 → {account}/{open_id}:\n{text}")
        return f"{account}/扣点率: 待推送 升至 {decision.eval_rate:.2%}（dry-run）"

    from web.alert_card_builder import build_fee_rate_card

    card = build_fee_rate_card(
        scope_display=scope.display_text, realtime=False,
        currency=decision.currency or "IDR",
        eval_rate=decision.eval_rate, baseline_rate=decision.baseline_rate,
        abs_change=decision.abs_change, eval_gmv=decision.eval_gmv,
        eval_window_label=_fmt_window(eval_start, eval_end),
        baseline_window_label=_fmt_window(baseline_start, baseline_end),
        board_url=_board_url(account),
        evidence=decision.evidence,
        policy_references=policy_refs,
    )
    sent = send_alert(account=account, open_id=open_id, text=text, card=card)
    if sent:
        upsert_fee_rate_alert_state(
            session,
            alert_type=fee_rate_alerts.ALERT_TYPE,
            account_id=account,
            scope_key=scope_id,
            last_window_end=eval_end,
            last_rate=decision.eval_rate,
            mark_sent=True,
        )
        session.commit()
        return f"{account}/扣点率: 已推送 升至 {decision.eval_rate:.2%}（基准 {decision.baseline_rate:.2%}）"
    return f"{account}/扣点率: 推送失败（游标不更新，下轮重试）"


def _scan_unsettled_fee_rate(session, *, account, open_id, scope, scope_id, dry_run: bool) -> str:
    """及时费率告警（预估口径）：最近 N 天 unsettled 预估费率 vs 历史已结算费率基准。

    与 _scan_fee_rate（结算口径，有滞后）互补：unsettled 反映平台**最新费率政策**，平台一调佣
    预估费率立即变 → **结算前**即可告警（会议痛点'月底才发现突然多收两三个点'）。
    去重：评估窗口结束日=昨天(T-1)，故每业务日最多报一次（独立 ALERT_TYPE_REALTIME 状态）。
    """
    today = business_today()
    # 评估期：最近 N 天未结算预估（无结算滞后）。**结束于昨天(T-1)**：当天(T)预估近乎为空
    # (≈0%覆盖,次日 01:13 同步才~90%,见 §7.1)，纳入会被单笔高费率误顶高 → 剔除当天。
    eval_end = today - timedelta(days=1)
    eval_start = eval_end - timedelta(days=settings.fee_rate_realtime_eval_days - 1)
    # 基准：已结算历史费率（避开滞后段，作稳基准）
    baseline_end = today - timedelta(days=settings.fee_rate_settle_lag_days)
    baseline_start = baseline_end - timedelta(days=settings.fee_rate_baseline_days - 1)

    common_scope = dict(
        platform=scope.platform, country=scope.country, shop_ids=scope.shop_ids or None
    )
    eval_by_ccy = get_unsettled_fee_rate(
        start_date=eval_start, end_date=eval_end, session=session, **common_scope
    )
    baseline_by_ccy = get_settled_fee_rate(
        start_date=baseline_start, end_date=baseline_end, session=session, **common_scope
    )

    decision = fee_rate_alerts.build_decision(
        eval_by_ccy=eval_by_ccy,
        baseline_by_ccy=baseline_by_ccy,
        scope_display=scope.display_text,
        min_gmv=settings.fee_rate_min_gmv,
        rel_pct=settings.fee_rate_alert_rel_pct,
        abs_pct=settings.fee_rate_alert_abs_pct,
        eval_window_label=_fmt_window(eval_start, eval_end),
        baseline_window_label=_fmt_window(baseline_start, baseline_end),
        realtime=True,
    )

    if not decision.should_alert:
        return f"{account}/及时费率: 不推送（{decision.skip_reason or '无异常'}）"

    # 去重：同一评估窗口（今天）已报过则不复读
    prev = get_fee_rate_alert_state(
        session, alert_type=fee_rate_alerts.ALERT_TYPE_REALTIME, account_id=account, scope_key=scope_id
    )
    if prev is not None and prev.last_window_end is not None and prev.last_window_end >= eval_end:
        return f"{account}/及时费率: 不推送（窗口 {eval_end} 已报过）"

    from services.tiktok_policy_evidence import get_policy_references

    policy_refs = get_policy_references(
        country=scope.country,
        fee_keys=fee_rate_alerts.evidence_fee_keys(decision.evidence),
        alert_date=eval_end,
    )
    text = fee_rate_alerts.enrich_message_with_evidence(
        decision.message or "",
        policy_references=policy_refs,
    )

    if dry_run:
        print(f"[alert][dry-run] 及时费率 → {account}/{open_id}:\n{text}")
        return f"{account}/及时费率: 待推送 预估升至 {decision.eval_rate:.2%}（dry-run）"

    from web.alert_card_builder import build_fee_rate_card

    card = build_fee_rate_card(
        scope_display=scope.display_text, realtime=True,
        currency=decision.currency or "IDR",
        eval_rate=decision.eval_rate, baseline_rate=decision.baseline_rate,
        abs_change=decision.abs_change, eval_gmv=decision.eval_gmv,
        eval_window_label=_fmt_window(eval_start, eval_end),
        baseline_window_label=_fmt_window(baseline_start, baseline_end),
        board_url=_board_url(account),
        evidence=decision.evidence,
        policy_references=policy_refs,
    )
    sent = send_alert(account=account, open_id=open_id, text=text, card=card)
    if sent:
        upsert_fee_rate_alert_state(
            session,
            alert_type=fee_rate_alerts.ALERT_TYPE_REALTIME,
            account_id=account,
            scope_key=scope_id,
            last_window_end=eval_end,
            last_rate=decision.eval_rate,
            mark_sent=True,
        )
        session.commit()
        return f"{account}/及时费率: 已推送 预估升至 {decision.eval_rate:.2%}（基准 {decision.baseline_rate:.2%}）"
    return f"{account}/及时费率: 推送失败（游标不更新，下轮重试）"


def _scan_hotsell(session, *, account, open_id, scope, scope_id, dry_run: bool) -> str:
    """爆单规则：某商品当日已付款销量破阈值即提醒。当日去重。返回一行状态。"""
    today = business_today()
    units_by_product = get_units_by_product(
        start_date=today, end_date=today,
        platform=scope.platform, country=scope.country, shop_ids=scope.shop_ids or None,
        session=session,
    )
    # 近 N 天上线的新品集合 → 文案对命中的爆单商品标注 🌟「新品爆发」（同阈不重复推送）
    new_ids = get_new_product_ids(
        as_of=today, lookback_days=get_config_int("new_product_lookback_days", account_id=account),
        platform=scope.platform, country=scope.country, shop_ids=scope.shop_ids or None,
        session=session,
    )
    prev = get_hotsell_alert_state(
        session, alert_type=hotsell_alerts.ALERT_TYPE, account_id=account, scope_key=scope_id
    )
    prev_ids = get_hotsell_reported_ids(prev, today)

    decision = hotsell_alerts.build_decision(
        units_by_product=units_by_product,
        threshold=get_config_int("hotsell_daily_units_threshold", account_id=account),
        prev_reported_ids=prev_ids,
        scope_display=scope.display_text,
        date_label=f"{today.month}/{today.day}",
        new_product_ids=new_ids,
    )

    if decision.should_alert:
        if dry_run:
            print(f"[alert][dry-run] 爆单 → {account}/{open_id}:\n{decision.message}")
            return f"{account}/爆单: 待推送 新爆款={len(decision.new_products)}（dry-run）"
        from web.alert_card_builder import build_hotsell_card

        card = build_hotsell_card(
            scope_display=scope.display_text,
            date_label=f"{today.month}/{today.day}",
            threshold=decision.threshold,
            new_products=[
                {"product_id": p.get("product_id"), "units": p.get("units"),
                 "name": p.get("product_name"), "is_new": p.get("is_new")}
                for p in decision.new_products
            ],
            board_url=_board_url(account),
        )
        sent = send_alert(account=account, open_id=open_id, text=decision.message, card=card)
        if sent:
            upsert_hotsell_alert_state(
                session,
                alert_type=hotsell_alerts.ALERT_TYPE,
                account_id=account,
                scope_key=scope_id,
                report_date=today,
                reported_product_ids=decision.new_reported_ids,
                mark_sent=True,
            )
            session.commit()
            return f"{account}/爆单: 已推送 新爆款={len(decision.new_products)}"
        return f"{account}/爆单: 推送失败（游标不更新，下轮重试）"

    # 不推：当日破阈集合有变化则更新游标（保持当天状态）
    if not dry_run and sorted(decision.new_reported_ids) != sorted(prev_ids):
        upsert_hotsell_alert_state(
            session,
            alert_type=hotsell_alerts.ALERT_TYPE,
            account_id=account,
            scope_key=scope_id,
            report_date=today,
            reported_product_ids=decision.new_reported_ids,
        )
        session.commit()
    return f"{account}/爆单: 不推送 当日破阈={len(decision.new_reported_ids)}"


def _resolve_recipient_scope(account: str, open_id: str, subscription_scope_key):
    """收件人的告警数据范围 = 用户授权范围(user_roles) ∩ 订阅范围(alert_recipients.scope_key)。

    权限 vs 订阅两个正交概念：
    - 权限(上限)：user_roles 是唯一真相源——boss=租户全部可见店；operator=allowed_scope_key。
      与看板/对话同一套授权(get_user_permission / resolve_authorized_scope 语义一致)。
    - 订阅(收窄)：alert_recipients.scope_key 只作为权限内的过滤器——NULL=全授权范围；
      配了 scope 则与授权范围取交集，**永不放大**（旧实现 scope_key 独立解析可旁路权限，已废）。

    返回 (scope, skip_reason)：无 user_roles 行/已停用/operator 未配范围 → (None, 原因)
    fail-closed 跳过该收件人（绝不回落全量）。
    """
    from services.user_authz import get_user_permission, resolve_authorized_scope, AuthzError

    perm = get_user_permission(open_id, account_id=account)
    if perm is None:
        return None, "无 user_roles 记录或已停用（fail-closed 跳过，请在用户管理登记）"
    try:
        # 订阅 scope 作为"请求范围"交给权限闸夹紧：boss 在租户内解析、operator 与 allowed 取交集，
        # 越界店铺由 resolve_filters 抛 ScopeError —— 与看板 ?scope= 同一条收窄路径。
        scope = resolve_authorized_scope(perm, requested_scope_key=subscription_scope_key or None)
    except (ScopeError, AuthzError) as exc:
        return None, f"订阅范围解析失败（{exc}）"
    if not scope.shop_ids:
        return None, "授权∩订阅后店铺集为空（fail-closed 跳过）"
    return scope, None


def _scan_one(recipient: dict, *, dry_run: bool) -> list[str]:
    """评估一个收件人的全部告警规则（待发货 + 库存 + 扣点率 + 及时费率 + 爆单）。返回各规则状态行列表。"""
    from core.tenancy import set_current_account

    account = recipient["account"]
    open_id = recipient["open_id"]
    scope_id = recipient.get("scope_id")
    set_current_account(account)  # ORM 自动过滤按本收件人租户隔离

    # 范围 = 用户授权(user_roles，与看板同源) ∩ 订阅(scope_id，可选收窄)；解析失败 fail-closed。
    scope, skip_reason = _resolve_recipient_scope(account, open_id, scope_id)
    if scope is None:
        msg = f"{account}/{open_id[-6:]}: 跳过（{skip_reason}）"
        print(f"[alert] {msg}")
        return [msg]
    session = SessionLocal()
    try:
        lines = []
        for rule in (
            _scan_fulfillment, _scan_stock, _scan_fee_rate, _scan_unsettled_fee_rate, _scan_hotsell
        ):
            try:
                lines.append(
                    rule(
                        session,
                        account=account,
                        open_id=open_id,
                        scope=scope,
                        scope_id=scope_id,
                        dry_run=dry_run,
                    )
                )
            except Exception as exc:  # 单条规则失败不影响其他规则
                session.rollback()
                lines.append(f"{account}/{rule.__name__}: 异常 {exc}")
        return lines
    finally:
        session.close()


def scan_fulfillment_alerts_flow(dry_run: bool = False):
    """待发货超时监控巡检主流程。dry_run=True 时只打印文案、不实发、不写游标。"""
    if is_quiet_now() and not dry_run:
        print(f"[alert] 静默时段（{settings.alert_quiet_start}~{settings.alert_quiet_end} "
              f"{settings.alert_quiet_tz}），跳过本轮巡检")
        return []

    results = []
    for recipient in load_recipients():
        try:
            results.extend(_scan_one(recipient, dry_run=dry_run))
        except Exception as exc:  # 单个收件人失败不影响其他人
            msg = f"{recipient.get('account')}: 异常 {exc}"
            print(f"[alert] {msg}")
            results.append(msg)
    for line in results:
        print(f"[alert] {line}")
    return results


if __name__ == "__main__":
    import sys

    # --dry-run：只打印判定文案、不实发、不写游标（上线前手动验证用，见 docs/ops-runbook.md）
    scan_fulfillment_alerts_flow(dry_run="--dry-run" in sys.argv)
