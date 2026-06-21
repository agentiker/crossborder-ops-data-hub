"""Phase 6 迁移：alert_recipients 建表 + seed 现有收件人。**幂等**，可重复跑。

1. init_db()（create_all）建 alert_recipients 表——只建不存在的表，已存在则跳过。
2. 把原写死在 flows/scan_fulfillment_alerts._FALLBACK_RECIPIENTS 的收件人 upsert 入库
   （按 (channel, account_id, open_id) 命中即更新、否则插入）。

用法：
  uv run python -m scripts.migrate_phase6_alert_recipients            # 执行
  uv run python -m scripts.migrate_phase6_alert_recipients --dry-run  # 只打印
"""
from __future__ import annotations

import argparse

from core.db import SessionLocal, init_db
from flows.scan_fulfillment_alerts import _FALLBACK_RECIPIENTS
from models.base_models import AlertRecipient


def migrate(dry_run: bool = False) -> int:
    actions: list[str] = []

    if not dry_run:
        init_db()  # 建 alert_recipients 表（幂等，已存在不动）
    actions.append("init_db()：确保 alert_recipients 表存在")

    session = SessionLocal()
    try:
        for r in _FALLBACK_RECIPIENTS:
            account, open_id, scope_id = r["account"], r["open_id"], r.get("scope_id")
            row = (
                session.query(AlertRecipient)
                .filter(
                    AlertRecipient.channel == "feishu",
                    AlertRecipient.account_id == account,
                    AlertRecipient.open_id == open_id,
                )
                .first()
            )
            if row:
                actions.append(f"[skip] 已存在收件人 [{account}] {open_id}")
                continue
            actions.append(f"INSERT 收件人 [{account}] {open_id} scope={scope_id}")
            if not dry_run:
                session.add(AlertRecipient(
                    channel="feishu", account_id=account, open_id=open_id,
                    scope_key=scope_id, is_active=True,
                ))
        if not dry_run:
            session.commit()
    finally:
        session.close()

    print(("[DRY-RUN] " if dry_run else "") + "迁移动作：")
    for a in actions:
        print("  -", a)
    print("（dry-run，未实际改库）" if dry_run else "完成。")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Phase 6 alert_recipients 迁移（幂等）")
    p.add_argument("--dry-run", action="store_true", help="只打印将做什么，不改库")
    raise SystemExit(migrate(dry_run=p.parse_args().dry_run))
