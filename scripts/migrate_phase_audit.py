"""审计合规迁移：建 api_call_logs + audit_log 两表。**幂等**，可重复跑。

只 init_db()（create_all）建两张新审计表——已存在则跳过。token 列改 EncryptedText 是
**应用层透明加解密**（impl=Text，DB 仍是 TEXT 列），无 DDL 变更；存量明文 token 的密文化
由 scripts/migrate_encrypt_tokens.py 单独处理。

用法：
  uv run python -m scripts.migrate_phase_audit            # 执行
  uv run python -m scripts.migrate_phase_audit --dry-run  # 只打印
"""
from __future__ import annotations

import argparse

from sqlalchemy import inspect

from core.db import engine, init_db

_TABLES = ["api_call_logs", "audit_log"]


def migrate(dry_run: bool = False) -> int:
    insp = inspect(engine)
    existing = {t: insp.has_table(t) for t in _TABLES}
    actions: list[str] = []
    for t in _TABLES:
        actions.append(f"[skip] {t} 已存在" if existing[t] else f"CREATE TABLE {t}")

    if not dry_run:
        init_db()  # create_all 只建不存在的表（含两审计表），幂等

    print(("[DRY-RUN] " if dry_run else "") + "迁移动作：")
    for a in actions:
        print("  -", a)
    print("（dry-run，未实际改库）" if dry_run else "完成。")
    return 0


if __name__ == "__main__":
    from core.tenancy import TENANT_BYPASS, set_current_account

    set_current_account(TENANT_BYPASS)  # 迁移脚本跨租户
    p = argparse.ArgumentParser(description="审计合规建表迁移（幂等）")
    p.add_argument("--dry-run", action="store_true", help="只打印将做什么，不改库")
    raise SystemExit(migrate(dry_run=p.parse_args().dry_run))
