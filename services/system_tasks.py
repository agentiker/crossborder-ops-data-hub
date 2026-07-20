"""Read-only scheduled task inventory for the WebUI.

This module intentionally exposes a fixed task catalog and only runs fixed
read commands. Frontend input must never choose a systemd unit or shell command.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from core.config import settings
from core.db import SessionLocal


@dataclass(frozen=True)
class TaskSpec:
    id: str
    name: str
    group: str
    kind: str
    source: str
    unit: Optional[str] = None
    cron_name_suffix: Optional[str] = None
    capability_id: Optional[str] = None
    touches_customer: bool = False
    business_visible: bool = True
    operator_visible: bool = True
    description: str = ""


TASKS: tuple[TaskSpec, ...] = (
    TaskSpec(id="sync-orders", name="订单同步", group="数据同步", kind="systemd", source="systemd", unit="data-sync-orders.timer", capability_id="订单数据", description="每 15 分钟同步 TikTok 订单"),
    TaskSpec(id="sync-inventory", name="库存同步", group="数据同步", kind="systemd", source="systemd", unit="data-sync-inventory.timer", capability_id="库存数据", description="每 15 分钟同步 TikTok 库存"),
    TaskSpec(id="sync-fulfillments", name="待发货同步", group="数据同步", kind="systemd", source="systemd", unit="data-sync-fulfillments.timer", capability_id="履约数据", description="每 15 分钟同步待发货快照"),
    TaskSpec(id="sync-ad-spend", name="广告消耗同步", group="数据同步", kind="systemd", source="systemd", unit="data-sync-ad-spend.timer", capability_id="广告数据", description="每日两次同步结算口径广告费用"),
    TaskSpec(id="sync-unsettled-fees", name="未结算费用同步", group="数据同步", kind="systemd", source="systemd", unit="data-sync-unsettled-fees.timer", capability_id="费用数据", description="每日同步未结算预估费用"),
    TaskSpec(id="sync-sku-variants", name="SKU 变体同步", group="数据同步", kind="systemd", source="systemd", unit="data-sync-sku-variants.timer", capability_id="商品主数据", description="每日同步颜色、尺码、款号等 SKU 维度"),
    TaskSpec(id="sync-mabang-costs", name="马帮成本同步", group="数据同步", kind="systemd", source="systemd", unit="data-sync-mabang-costs.timer", capability_id="成本数据", description="每周同步马帮成本"),
    TaskSpec(id="sync-exchange-rate", name="汇率同步", group="数据同步", kind="systemd", source="systemd", unit="data-sync-exchange-rate.timer", capability_id="汇率数据", description="工作日同步中行外汇牌价"),
    TaskSpec(id="refresh-tokens", name="Token 刷新", group="系统维护", kind="systemd", source="systemd", unit="data-refresh-tokens.timer", capability_id="授权维护", business_visible=False, operator_visible=False, description="每 6 小时刷新平台 token"),
    TaskSpec(id="aggregate-profit", name="预估利润聚合", group="经营计算", kind="systemd", source="systemd", unit="data-aggregate-profit.timer", capability_id="利润数据", description="每日聚合昨日预估利润"),
    TaskSpec(id="backfill-settled-profit", name="结算利润回填", group="经营计算", kind="systemd", source="systemd", unit="data-backfill-settled-profit.timer", capability_id="利润数据", description="每日回填结算完整历史天利润"),
    TaskSpec(id="anchor-audit", name="审计链锚定", group="系统维护", kind="systemd", source="systemd", unit="data-anchor-audit.timer", capability_id="审计", touches_customer=True, business_visible=False, operator_visible=False, description="每日锚定审计链尾并发送运维留痕"),
    TaskSpec(id="verify-audit-chain", name="审计链校验", group="系统维护", kind="systemd", source="systemd", unit="data-verify-audit-chain.timer", capability_id="审计", touches_customer=True, business_visible=False, operator_visible=False, description="每 6 小时校验审计链完整性"),
    TaskSpec(id="backup-db", name="数据库备份", group="系统维护", kind="systemd", source="systemd", unit="data-backup-db.timer", capability_id="备份", business_visible=False, operator_visible=False, description="每日加密数据库备份"),
    TaskSpec(id="scan-alerts", name="告警总巡检", group="主动推送", kind="systemd", source="systemd", unit="data-scan-alerts.timer", capability_id="经营告警", touches_customer=True, description="待发货、库存、费率、爆单告警巡检"),
    TaskSpec(id="push-replenishment", name="补货采购单", group="主动推送", kind="systemd", source="systemd", unit="data-push-replenishment.timer", capability_id="补货提醒", touches_customer=True, description="每日推送补货采购单"),
    TaskSpec(id="daily-report", name="每日经营日报", group="主动推送", kind="openclaw", source="openclaw", capability_id="经营日报", touches_customer=True, description="openclaw cron 生成并投递日报"),
    TaskSpec(id="weekly-report", name="每周经营周报", group="主动推送", kind="openclaw", source="openclaw", capability_id="经营周报", touches_customer=True, description="openclaw cron 生成并投递周报"),
)


CAPABILITIES: tuple[dict, ...] = (
    {"id": "core-sync", "name": "核心数据同步", "group": "数据同步", "task_ids": ["sync-orders", "sync-inventory", "sync-fulfillments", "sync-sku-variants"], "touches_customer": False},
    {"id": "finance-sync", "name": "费用与利润链路", "group": "经营计算", "task_ids": ["sync-ad-spend", "sync-unsettled-fees", "aggregate-profit", "backfill-settled-profit"], "touches_customer": False},
    {"id": "stock-alerts", "name": "库存/履约/费率告警", "group": "主动推送", "task_ids": ["scan-alerts"], "touches_customer": True},
    {"id": "replenishment", "name": "补货采购单", "group": "主动推送", "task_ids": ["push-replenishment"], "touches_customer": True},
    {"id": "reports", "name": "经营日报/周报", "group": "主动推送", "task_ids": ["daily-report", "weekly-report"], "touches_customer": True},
    {"id": "ops-maintenance", "name": "系统维护", "group": "系统维护", "task_ids": ["refresh-tokens", "anchor-audit", "verify-audit-chain", "backup-db"], "touches_customer": True},
)


def _run(
    args: list[str],
    *,
    timeout: int = 8,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)


def _clean_log(text: str) -> str:
    lines = []
    for raw in (text or "").splitlines()[-6:]:
        line = raw.strip()
        if not line:
            continue
        lowered = line.lower()
        if any(k in lowered for k in ("token=", "secret=", "password=", "authorization:", "cookie:")):
            line = "[redacted sensitive log line]"
        lines.append(line[-360:])
    return "\n".join(lines)


def _parse_systemctl_show(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in (text or "").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _format_schedule(value) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        expr = value.get("expr") or value.get("cron") or value.get("schedule")
        tz = value.get("tz") or value.get("timezone")
        if expr and tz:
            return f"{expr} ({tz})"
        if expr:
            return str(expr)
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _parse_task_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    for tz_suffix in (" CST", " UTC"):
        if text.endswith(tz_suffix):
            text = text[: -len(tz_suffix)]
            break
    for fmt in ("%a %Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19] if "T" in fmt else text, fmt)
        except ValueError:
            continue
    return None


def _systemd_state(unit: str) -> dict:
    out = {
        "enabled": False,
        "active": False,
        "status": "unknown",
        "last_run": None,
        "next_run": None,
        "last_result": "unknown",
        "log_excerpt": "",
        "error": None,
    }
    try:
        show = _run([
            "systemctl", "--user", "show", unit,
            "--property=LoadState,UnitFileState,ActiveState,SubState,LastTriggerUSec,NextElapseUSecRealtime",
        ])
        if show.returncode != 0:
            out["error"] = (show.stderr or show.stdout).strip()[:240]
            return out
        props = _parse_systemctl_show(show.stdout)
        load = props.get("LoadState", "")
        enabled = props.get("UnitFileState", "")
        active = props.get("ActiveState", "")
        sub = props.get("SubState", "")
        last_run = props.get("LastTriggerUSec", "")
        next_run = props.get("NextElapseUSecRealtime", "")
        out["enabled"] = enabled == "enabled"
        out["active"] = active in ("active", "activating")
        out["last_run"] = None if last_run in ("", "n/a") else last_run
        out["next_run"] = None if next_run in ("", "n/a") else next_run
        if load != "loaded":
            out["status"] = "missing"
        elif not out["enabled"]:
            out["status"] = "disabled"
        elif active == "failed" or sub == "failed":
            out["status"] = "failed"
        else:
            out["status"] = "ok"

        service = unit[:-6] + ".service" if unit.endswith(".timer") else unit
        svc = _run([
            "systemctl", "--user", "show", service,
            "--property=Result,ExecMainStatus,ActiveState,SubState",
        ])
        if svc.returncode == 0:
            result = _parse_systemctl_show(svc.stdout).get("Result") or "unknown"
            out["last_result"] = result
            if result not in ("success", "unknown", ""):
                out["status"] = "failed"
        log = _run(["journalctl", "--user", "-u", service, "-n", "20", "--no-pager"], timeout=10)
        if log.returncode == 0:
            out["log_excerpt"] = _clean_log(log.stdout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["status"] = "degraded"
        out["error"] = str(exc)[:240]
    return out


def _openclaw_jobs() -> list[dict]:
    candidates = [settings.openclaw_bin, "openclaw"]
    for cmd in candidates:
        if not cmd:
            continue
        env = os.environ.copy()
        bin_dir = os.path.dirname(cmd)
        if bin_dir:
            env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        try:
            result = _run([cmd, "cron", "list", "--json"], timeout=8, env=env)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        try:
            obj = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            continue
        return obj if isinstance(obj, list) else list(obj.get("jobs") or [])
    return []


def _cron_state(spec: TaskSpec, jobs: list[dict], account_id: str) -> dict:
    keyword = "每日经营日报" if spec.id == "daily-report" else "每周经营周报"
    tenant_jobs = [
        j for j in jobs
        if keyword in str(j.get("name") or "") and str(j.get("account") or j.get("account_id") or account_id) in (account_id, "")
    ]
    job = tenant_jobs[0] if tenant_jobs else None
    if job is None:
        return {
            "enabled": False, "active": False, "status": "missing",
            "schedule": None, "last_run": None, "next_run": None,
            "last_result": "missing", "log_excerpt": "", "error": None,
        }
    status = str(job.get("status") or "").lower()
    enabled = not bool(job.get("disabled")) and status not in ("disabled", "paused")
    return {
        "enabled": enabled,
        "active": enabled,
        "status": "failed" if status in ("error", "failed") else ("ok" if enabled else "disabled"),
        "schedule": _format_schedule(job.get("schedule") or job.get("cron") or job.get("cron_expr")),
        "last_run": job.get("last") or job.get("last_run") or job.get("lastRunAt"),
        "next_run": job.get("next") or job.get("next_run") or job.get("nextRunAt"),
        "last_result": status or "unknown",
        "log_excerpt": str(job.get("delivery") or job.get("last_error") or "")[:360],
        "error": None,
    }


def _recipient_summary(account_id: str) -> dict:
    session = SessionLocal()
    try:
        from models.base_models import AlertRecipient, UserRole

        rows = (
            session.query(AlertRecipient, UserRole.name)
            .outerjoin(
                UserRole,
                (UserRole.channel == AlertRecipient.channel)
                & (UserRole.account_id == AlertRecipient.account_id)
                & (UserRole.open_id == AlertRecipient.open_id)
                & (UserRole.is_active.is_(True)),
            )
            .filter(
                AlertRecipient.account_id == account_id,
                AlertRecipient.is_active.is_(True),
            )
            .order_by(UserRole.name, AlertRecipient.open_id)
            .all()
        )
        names = [
            (name or recipient.note or recipient.open_id).strip()
            for recipient, name in rows
            if (name or recipient.note or recipient.open_id)
        ]
        return {"active_recipients": len(rows), "recipient_names": names}
    except Exception:
        return {"active_recipients": None, "recipient_names": []}
    finally:
        session.close()


def get_system_task_snapshot(*, account_id: str, role: str) -> dict:
    is_boss = role == "boss"
    jobs = _openclaw_jobs()
    recipients = _recipient_summary(account_id)
    tasks = []
    for spec in TASKS:
        if not is_boss and not spec.operator_visible:
            continue
        if spec.source == "systemd" and spec.unit:
            state = _systemd_state(spec.unit)
            schedule = None
        else:
            state = _cron_state(spec, jobs, account_id)
            schedule = state.pop("schedule", None)
        task = {
            "id": spec.id,
            "name": spec.name,
            "group": spec.group,
            "kind": spec.kind,
            "source": spec.source,
            "unit": spec.unit if is_boss else None,
            "description": spec.description,
            "capability_id": spec.capability_id,
            "touches_customer": spec.touches_customer,
            "business_visible": spec.business_visible,
            "schedule": schedule,
            **state,
        }
        if spec.id in ("scan-alerts", "push-replenishment"):
            task["recipient_summary"] = recipients
        if not is_boss:
            task["log_excerpt"] = ""
            task["error"] = None
        tasks.append(task)

    by_id = {t["id"]: t for t in tasks}
    capabilities = []
    for cap in CAPABILITIES:
        cap_tasks = [by_id[i] for i in cap["task_ids"] if i in by_id]
        if not cap_tasks:
            continue
        failed = [t for t in cap_tasks if t["status"] in ("failed", "degraded")]
        enabled_count = sum(1 for t in cap_tasks if t["enabled"])
        capabilities.append({
            "id": cap["id"],
            "name": cap["name"],
            "group": cap["group"],
            "touches_customer": cap["touches_customer"],
            "enabled": enabled_count > 0 and not failed,
            "status": "failed" if failed else ("ok" if enabled_count else "disabled"),
            "summary": f"{enabled_count}/{len(cap_tasks)} 个底层任务启用",
            "task_ids": [t["id"] for t in cap_tasks],
        })

    failed_count = sum(1 for t in tasks if t["status"] in ("failed", "degraded"))
    next_candidates = [
        (parsed, t)
        for t in tasks
        if (parsed := _parse_task_time(t.get("next_run"))) is not None
    ]
    next_task = min(next_candidates, key=lambda item: item[0])[1] if next_candidates else None
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "account_id": account_id,
        "role": role,
        "summary": {
            "total": len(tasks),
            "enabled": sum(1 for t in tasks if t["enabled"]),
            "failed": failed_count,
            "customer_touching": sum(1 for t in tasks if t["touches_customer"]),
            "next_run": next_task.get("next_run") if next_task else None,
            "next_run_task_id": next_task.get("id") if next_task else None,
            "next_run_task_name": next_task.get("name") if next_task else None,
        },
        "capabilities": capabilities,
        "runs": tasks,
    }
