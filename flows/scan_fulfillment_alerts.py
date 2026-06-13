"""监控告警巡检 flow：确定性判定 + openclaw message send 直投飞书（0 经 LLM）。

覆盖两类告警，每个收件人每轮各自独立判定/去重/投递（互不影响）：
1. 待发货超时（services.fulfillment_metrics + fulfillment_alerts）。
2. 低库存 / 断货（services.stock_metrics + stock_alerts）。

调度：systemd timer data-scan-alerts（默认每 30 分钟）；本地调试 `python main.py --task alert-scan`。
链路（每条规则）：静默时段跳过 → 取数（确定性分桶）→ build_decision 判定+组装文案 →
      该推则 subprocess 调 `openclaw message send` → 投递成功后写回去重游标。

为什么不用 openclaw cron：cron job 必经 agent/LLM，而告警要阈值/去重/稳定文案、每跑必准。
openclaw 在这里只当飞书出站通道（message send 走本地 gateway RPC，不出网、不经 agent）。
收件人/范围当前写死在 RECIPIENTS（单租户阶段）；多租户后可迁 DB/config（见 plan/09）。
（文件名沿用 scan_fulfillment_alerts 以免动 timer/main/prefect 引用；现已是「告警总巡检」。）
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

from prefect import flow

from core.config import settings
from core.db import SessionLocal
from services import stock_alerts
from services.fulfillment_alerts import ALERT_TYPE, build_decision
from services.fulfillment_metrics import get_pending_fulfillments
from services.metrics_store import (
    get_fulfillment_alert_state,
    get_stock_alert_state,
    get_stock_reported_skus,
    upsert_fulfillment_alert_state,
    upsert_stock_alert_state,
)
from services.stock_metrics import get_stock_risk
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
        sent = send_feishu_message(
            account=account, open_id=open_id, text=decision.message
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


def _scan_one(recipient: dict, *, dry_run: bool) -> list[str]:
    """评估一个收件人的全部告警规则（待发货 + 库存）。返回各规则状态行列表。"""
    account = recipient["account"]
    open_id = recipient["open_id"]
    scope_id = recipient.get("scope_id")

    scope = resolve_filters(scope_key=scope_id)
    session = SessionLocal()
    try:
        lines = []
        for rule in (_scan_fulfillment, _scan_stock):
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
            results.extend(_scan_one(recipient, dry_run=dry_run))
        except Exception as exc:  # 单个收件人失败不影响其他人
            msg = f"{recipient.get('account')}: 异常 {exc}"
            print(f"[alert] {msg}")
            results.append(msg)
    for line in results:
        print(f"[alert] {line}")
    return results


if __name__ == "__main__":
    scan_fulfillment_alerts_flow()
