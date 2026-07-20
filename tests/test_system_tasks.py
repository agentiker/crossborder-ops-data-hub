from __future__ import annotations

import subprocess

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.db import Base
from models.base_models import AlertRecipient, UserRole
from services import system_tasks


def _cp(stdout: str = "", stderr: str = "", rc: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


def test_recipient_summary_returns_user_names(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(system_tasks, "SessionLocal", Session)

    with Session() as session:
        session.add(AlertRecipient(channel="feishu", account_id="ecom-app", open_id="ou_boss", is_active=True))
        session.add(AlertRecipient(channel="feishu", account_id="ecom-app", open_id="ou_op", is_active=True))
        session.add(UserRole(channel="feishu", account_id="ecom-app", open_id="ou_boss", role="boss", name="老板", is_active=True))
        session.add(UserRole(channel="feishu", account_id="ecom-app", open_id="ou_op", role="operator", name="运营", is_active=True))
        session.commit()

    out = system_tasks._recipient_summary("ecom-app")

    assert out == {"active_recipients": 2, "recipient_names": ["老板", "运营"]}


def test_systemd_state_ok(monkeypatch):
    def fake_run(args, timeout=8):
        if args[:3] == ["systemctl", "--user", "show"] and args[3].endswith(".timer"):
            return _cp(
                "LoadState=loaded\n"
                "UnitFileState=enabled\n"
                "ActiveState=active\n"
                "SubState=waiting\n"
                "LastTriggerUSec=Sun 2026-07-19 08:40:00 CST\n"
                "NextElapseUSecRealtime=Sun 2026-07-19 09:10:00 CST\n"
            )
        if args[:3] == ["systemctl", "--user", "show"]:
            return _cp("Result=success\nExecMainStatus=0\nActiveState=inactive\nSubState=dead\n")
        return _cp("Finished data-scan-alerts.service\n[alert] 库存: 不推送 风险=0\n")

    monkeypatch.setattr(system_tasks, "_run", fake_run)
    out = system_tasks._systemd_state("data-scan-alerts.timer")

    assert out["enabled"] is True
    assert out["status"] == "ok"
    assert out["last_result"] == "success"
    assert "库存" in out["log_excerpt"]


def test_snapshot_operator_hides_maintenance(monkeypatch):
    monkeypatch.setattr(system_tasks, "_openclaw_jobs", lambda: [])
    monkeypatch.setattr(system_tasks, "_recipient_summary", lambda account_id: {"active_recipients": 2})
    monkeypatch.setattr(system_tasks, "_systemd_state", lambda unit: {
        "enabled": True,
        "active": True,
        "status": "ok",
        "last_run": "last",
        "next_run": "next",
        "last_result": "success",
        "log_excerpt": "secret",
        "error": None,
    })

    out = system_tasks.get_system_task_snapshot(account_id="ecom-app", role="operator")
    ids = {r["id"] for r in out["runs"]}

    assert "backup-db" not in ids
    assert "refresh-tokens" not in ids
    assert "sync-orders" in ids
    assert all(r["log_excerpt"] == "" for r in out["runs"])


def test_openclaw_schedule_dict_is_formatted():
    out = system_tasks._cron_state(
        system_tasks.TaskSpec(
            id="daily-report",
            name="每日经营日报",
            group="主动推送",
            kind="openclaw",
            source="openclaw",
        ),
        jobs=[{
            "name": "每日经营日报",
            "status": "enabled",
            "schedule": {"kind": "cron", "expr": "30 8 * * *", "tz": "Asia/Shanghai"},
        }],
        account_id="ecom-app",
    )

    assert out["schedule"] == "30 8 * * * (Asia/Shanghai)"


def test_summary_next_run_uses_real_time_order(monkeypatch):
    monkeypatch.setattr(system_tasks, "_openclaw_jobs", lambda: [])
    monkeypatch.setattr(system_tasks, "_recipient_summary", lambda account_id: {"active_recipients": 0})

    def fake_state(unit):
        if unit == "data-sync-inventory.timer":
            return {
                "enabled": True,
                "active": True,
                "status": "ok",
                "last_run": None,
                "next_run": "Sun 2026-07-19 10:47:00 CST",
                "last_result": "success",
                "log_excerpt": "",
                "error": None,
            }
        return {
            "enabled": True,
            "active": True,
            "status": "ok",
            "last_run": None,
            "next_run": "Mon 2026-07-20 01:13:00 CST",
            "last_result": "success",
            "log_excerpt": "",
            "error": None,
        }

    monkeypatch.setattr(system_tasks, "_systemd_state", fake_state)
    out = system_tasks.get_system_task_snapshot(account_id="ecom-app", role="boss")

    assert out["summary"]["next_run"] == "Sun 2026-07-19 10:47:00 CST"
    assert out["summary"]["next_run_task_id"] == "sync-inventory"
    assert out["summary"]["next_run_task_name"] == "库存同步"
