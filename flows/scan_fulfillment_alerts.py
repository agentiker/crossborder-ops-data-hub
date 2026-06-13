"""待发货超时监控巡检 flow：确定性判定 + openclaw message send 直投飞书（0 经 LLM）。

调度：prefect.yaml 的 scan-fulfillment-alerts（默认每 30 分钟）；本地调试 `python main.py --task alert-scan`。
链路：遍历收件人 → 静默时段跳过 → 复用 get_pending_fulfillments 取超时分桶 →
      build_decision 判定+组装文案 → 该推则 subprocess 调 `openclaw message send` →
      投递成功后写回去重游标（FulfillmentAlertState）。

为什么不用 openclaw cron：cron job 必经 agent/LLM，而告警要阈值/去重/稳定文案、每跑必准。
openclaw 在这里只当飞书出站通道（message send 走本地 gateway RPC，不出网、不经 agent）。
收件人/范围当前写死在 RECIPIENTS（单租户阶段）；多租户后可迁 DB/config（见 plan/09）。
"""
from __future__ import annotations

import subprocess
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

from prefect import flow

from core.config import settings
from core.db import SessionLocal
from services.fulfillment_alerts import ALERT_TYPE, build_decision
from services.fulfillment_metrics import get_pending_fulfillments
from services.metrics_store import (
    get_fulfillment_alert_state,
    upsert_fulfillment_alert_state,
)
from services.scope_resolution import resolve_filters

# 收件人配置（单租户阶段写死；scope_id=None → 全部已授权店）。
# account 对应 ~/.openclaw/openclaw.json 的 channels.feishu.accounts 键；open_id 为飞书用户 ou_xxx。
RECIPIENTS = [
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
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60
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


def _scan_one(recipient: dict, *, dry_run: bool) -> str:
    """评估并（必要时）投递一个收件人。返回一行状态用于汇总日志。"""
    account = recipient["account"]
    open_id = recipient["open_id"]
    scope_id = recipient.get("scope_id")

    scope = resolve_filters(scope_key=scope_id)
    metrics = get_pending_fulfillments(
        platform=scope.platform,
        country=scope.country,
        shop_ids=scope.shop_ids or None,
    )

    session = SessionLocal()
    try:
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
                print(f"[alert][dry-run] → {account}/{open_id}:\n{decision.message}")
                return f"{account}: 待推送 overdue={decision.overdue}（dry-run）"
            sent = send_feishu_message(
                account=account, open_id=open_id, text=decision.message
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
                return f"{account}: 已推送 overdue={decision.overdue}(+{decision.delta})"
            return f"{account}: 推送失败 overdue={decision.overdue}（游标不更新，下轮重试）"

        if decision.reset_state and not dry_run and prev_reported != 0:
            # 超时单已清零：游标归零，让下一批新增能重新提醒。
            upsert_fulfillment_alert_state(
                session,
                alert_type=ALERT_TYPE,
                account_id=account,
                scope_key=scope_id,
                last_reported_overdue=0,
                last_critical=decision.critical,
            )
            session.commit()
        return f"{account}: 不推送 overdue={decision.overdue}"
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@flow(name="scan-fulfillment-alerts", log_prints=True)
def scan_fulfillment_alerts_flow(dry_run: bool = False):
    """待发货超时监控巡检主流程。dry_run=True 时只打印文案、不实发、不写游标。"""
    if is_quiet_now() and not dry_run:
        print(f"[alert] 静默时段（{settings.alert_quiet_start}~{settings.alert_quiet_end} "
              f"{settings.alert_quiet_tz}），跳过本轮巡检")
        return []

    results = []
    for recipient in RECIPIENTS:
        try:
            results.append(_scan_one(recipient, dry_run=dry_run))
        except Exception as exc:  # 单个收件人失败不影响其他人
            msg = f"{recipient.get('account')}: 异常 {exc}"
            print(f"[alert] {msg}")
            results.append(msg)
    for line in results:
        print(f"[alert] {line}")
    return results


if __name__ == "__main__":
    scan_fulfillment_alerts_flow()
