"""Phase 3 迁移：business_scopes 加 account_id（多租户隔离）。**幂等**，可重复跑。

做四件事（每步都先探测再执行，已完成则跳过）：
  1. business_scopes 加 `account_id` 列（VARCHAR(64) NOT NULL DEFAULT 'ecom-app'）+ 普通索引；
     存量行被 DEFAULT 回填为 ecom-app。
  2. 唯一约束从「scope_key 全局唯一」改为「(account_id, scope_key) 联合唯一」：
     丢掉 scope_key 上的旧唯一索引（保留/补一个普通索引供查找），新建联合唯一。
  3. platform_tokens 存量店（account_id IS NULL）回填 account_id='ecom-app'，确立店铺归属
     （现有那个印尼店归我方）。
  4. 为 gtl 租户（ecom-app-gtl）建一条 scope，显式授权它复用我方的店做测试（shop_ids 取
     ecom-app 的 tts-id-all）。这样隔离逻辑全程生效，gtl 的访问是一条可撤销的显式授权。

连库走 core.db.engine（读 .env 的 DB__*），与应用同一个库。

用法：
  uv run python -m scripts.migrate_phase3_scope_account            # 执行
  uv run python -m scripts.migrate_phase3_scope_account --dry-run  # 只打印将做什么
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import inspect, text

from core.db import engine

TABLE = "business_scopes"
GTL_ACCOUNT = "ecom-app-gtl"
SOURCE_ACCOUNT = "ecom-app"
GTL_SCOPE_KEY = "tts-id-all"


def _has_column(conn, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspect(conn).get_columns(table))


def _indexes(conn, table: str) -> list[dict]:
    return inspect(conn).get_indexes(table)


def _unique_single_col_indexes(conn, table: str, column: str) -> list[str]:
    """返回 table 上「仅含 column 一列且唯一」的索引名（即旧的全局唯一约束）。"""
    out = []
    for ix in _indexes(conn, table):
        if ix.get("unique") and list(ix.get("column_names") or []) == [column]:
            out.append(ix["name"])
    return out


def _has_index_on(conn, table: str, columns: list[str], *, unique: bool | None = None) -> bool:
    for ix in _indexes(conn, table):
        if list(ix.get("column_names") or []) == columns:
            if unique is None or bool(ix.get("unique")) == unique:
                return True
    return False


def migrate(dry_run: bool = False) -> int:
    actions: list[str] = []

    with engine.begin() as conn:
        # --- 1. 加 account_id 列 ---
        if not _has_column(conn, TABLE, "account_id"):
            actions.append(f"ALTER {TABLE} ADD COLUMN account_id VARCHAR(64) NOT NULL DEFAULT 'ecom-app'")
            if not dry_run:
                conn.execute(text(
                    f"ALTER TABLE {TABLE} ADD COLUMN account_id "
                    f"VARCHAR(64) NOT NULL DEFAULT 'ecom-app'"
                ))
        else:
            actions.append(f"[skip] {TABLE}.account_id 已存在")

        # 回填存量（DEFAULT 已处理新加列，这里兜底处理 NULL/空）
        if not dry_run and _has_column(conn, TABLE, "account_id"):
            conn.execute(text(
                f"UPDATE {TABLE} SET account_id='{SOURCE_ACCOUNT}' "
                f"WHERE account_id IS NULL OR account_id=''"
            ))

        # account_id 普通索引
        if not _has_index_on(conn, TABLE, ["account_id"]):
            actions.append(f"CREATE INDEX ix_business_scopes_account_id ON {TABLE}(account_id)")
            if not dry_run:
                conn.execute(text(f"CREATE INDEX ix_business_scopes_account_id ON {TABLE}(account_id)"))
        else:
            actions.append("[skip] account_id 索引已存在")

        # --- 2. 唯一约束 scope_key → (account_id, scope_key) ---
        for name in _unique_single_col_indexes(conn, TABLE, "scope_key"):
            actions.append(f"DROP 旧唯一索引 {name}（scope_key 全局唯一）")
            if not dry_run:
                conn.execute(text(f"ALTER TABLE {TABLE} DROP INDEX `{name}`"))

        # 保证 scope_key 仍有普通索引（查找用）
        if not _has_index_on(conn, TABLE, ["scope_key"]):
            actions.append(f"CREATE INDEX ix_business_scopes_scope_key ON {TABLE}(scope_key)")
            if not dry_run:
                conn.execute(text(f"CREATE INDEX ix_business_scopes_scope_key ON {TABLE}(scope_key)"))

        # 联合唯一
        if not _has_index_on(conn, TABLE, ["account_id", "scope_key"], unique=True):
            actions.append("CREATE UNIQUE uq_business_scope_account_key (account_id, scope_key)")
            if not dry_run:
                conn.execute(text(
                    f"ALTER TABLE {TABLE} ADD CONSTRAINT uq_business_scope_account_key "
                    f"UNIQUE (account_id, scope_key)"
                ))
        else:
            actions.append("[skip] 联合唯一 (account_id, scope_key) 已存在")

        # --- 3. platform_tokens 存量回填归属 ---
        n_null = conn.execute(text(
            "SELECT COUNT(*) FROM platform_tokens WHERE account_id IS NULL OR account_id=''"
        )).scalar()
        actions.append(f"platform_tokens 回填 account_id='{SOURCE_ACCOUNT}'：{n_null} 行")
        if not dry_run and n_null:
            conn.execute(text(
                f"UPDATE platform_tokens SET account_id='{SOURCE_ACCOUNT}' "
                f"WHERE account_id IS NULL OR account_id=''"
            ))

        # --- 4. 为 gtl 建测试 scope（复用 ecom-app 的 tts-id-all 店铺集合）---
        src_shop_json = conn.execute(text(
            f"SELECT shop_ids FROM {TABLE} "
            f"WHERE account_id='{SOURCE_ACCOUNT}' AND scope_key='{GTL_SCOPE_KEY}' LIMIT 1"
        )).scalar()
        exists_gtl = conn.execute(text(
            f"SELECT COUNT(*) FROM {TABLE} "
            f"WHERE account_id='{GTL_ACCOUNT}' AND scope_key='{GTL_SCOPE_KEY}'"
        )).scalar()
        if exists_gtl:
            actions.append(f"[skip] gtl scope [{GTL_ACCOUNT}]/{GTL_SCOPE_KEY} 已存在")
        elif src_shop_json is None:
            actions.append(f"[warn] 源 scope [{SOURCE_ACCOUNT}]/{GTL_SCOPE_KEY} 不存在，跳过建 gtl scope")
        else:
            actions.append(
                f"INSERT gtl scope [{GTL_ACCOUNT}]/{GTL_SCOPE_KEY}，shop_ids={src_shop_json}"
            )
            if not dry_run:
                conn.execute(
                    text(
                        f"INSERT INTO {TABLE} "
                        f"(account_id, scope_key, scope_name, scope_type, platform, country, shop_ids, is_active) "
                        f"SELECT :acc, scope_key, scope_name, scope_type, platform, country, shop_ids, is_active "
                        f"FROM {TABLE} WHERE account_id=:src AND scope_key=:key"
                    ),
                    {"acc": GTL_ACCOUNT, "src": SOURCE_ACCOUNT, "key": GTL_SCOPE_KEY},
                )

    print(("[DRY-RUN] " if dry_run else "") + "迁移动作：")
    for a in actions:
        print("  -", a)
    print("完成。" if not dry_run else "（dry-run，未实际改库）")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Phase 3 business_scopes account_id 迁移（幂等）")
    p.add_argument("--dry-run", action="store_true", help="只打印将做什么，不改库")
    raise SystemExit(migrate(dry_run=p.parse_args().dry_run))
