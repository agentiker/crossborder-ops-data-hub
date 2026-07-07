"""迁移：user_roles 加 name 列（用户名/飞书昵称），并从历史 note 的「申请人：X」回填。**幂等**，可重复跑。

背景：首登自动登记曾把飞书昵称塞进 note，写成 "申请人：{昵称}"（note 本该是运维自由备注，被塞了双重语义）。
现改为独立 name 列作唯一真相（用户名列 / 打招呼称呼都读它）。本迁移：
1. 加 name 列（NULL）——create_all 不给已存在的表加列，故手写 ALTER；
2. 回填：把历史 note 里「申请人：X」格式的行，X 写进 name，并**清空该 note**（它是我们塞的、非运维真备注，
   有了 name 列就冗余）；非该格式的 note（运维手写自由备注）原样保留、不动 name。

用法：
  uv run python -m scripts.migrate_user_role_name            # 执行
  uv run python -m scripts.migrate_user_role_name --dry-run  # 只打印
"""
from __future__ import annotations

import argparse

from sqlalchemy import inspect, text

from core.db import engine
from services.user_authz import parse_legacy_applicant_name


def migrate(dry_run: bool = False) -> int:
    actions: list[str] = []
    insp = inspect(engine)

    if not insp.has_table("user_roles"):
        actions.append("[info] user_roles 表不存在，由 init_db/create_all 按最新模型建表（已含 name）")
        _print(actions, dry_run)
        return 0

    existing_cols = {c["name"] for c in insp.get_columns("user_roles")}
    col_exists = "name" in existing_cols
    if col_exists:
        actions.append("[skip] user_roles.name 已存在")
    else:
        ddl = "ALTER TABLE user_roles ADD COLUMN name VARCHAR(64) NULL"
        actions.append(ddl)
        if not dry_run:
            with engine.begin() as conn:
                conn.execute(text(ddl))
            col_exists = True  # 已真加，后续回填可读 name

    # 回填：把历史 note 的「申请人：X」写进 name 并清空该 note。
    # 查询不引用 name 列（dry-run 时列可能还没建），幂等性靠 Python 判断已有 name。
    select_sql = (
        "SELECT id, note, name FROM user_roles WHERE note IS NOT NULL"
        if col_exists
        else "SELECT id, note, NULL AS name FROM user_roles WHERE note IS NOT NULL"
    )
    with engine.connect() as conn:
        rows = conn.execute(text(select_sql)).fetchall()

    backfilled = 0
    for row_id, note, cur_name in rows:
        if cur_name:  # 已有 name，幂等跳过
            continue
        parsed = parse_legacy_applicant_name(note)
        if not parsed:
            continue  # 运维自由备注，不动
        actions.append(f"[backfill] id={row_id}: name='{parsed}'，清空历史 note")
        backfilled += 1
        if not dry_run:
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE user_roles SET name = :name, note = NULL WHERE id = :id"),
                    {"name": parsed, "id": row_id},
                )
    if not backfilled:
        actions.append("[info] 无「申请人：X」格式的历史 note 需回填")

    _print(actions, dry_run)
    return 0


def _print(actions: list[str], dry_run: bool) -> None:
    print(("[DRY-RUN] " if dry_run else "") + "迁移动作：")
    for a in actions:
        print("  -", a)
    print("（dry-run，未实际改库）" if dry_run else "完成。")


if __name__ == "__main__":
    from core.tenancy import TENANT_BYPASS, set_current_account

    set_current_account(TENANT_BYPASS)  # 迁移脚本需跨租户操作
    p = argparse.ArgumentParser(description="user_roles 加 name 列 + 回填历史 note（幂等）")
    p.add_argument("--dry-run", action="store_true", help="只打印将做什么，不改库")
    raise SystemExit(migrate(dry_run=p.parse_args().dry_run))
