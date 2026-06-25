"""阶段3a 迁移：预估利润所需建表 + DailyProfit 加列。**幂等**，可重复跑。

1. init_db()（create_all）建 fact_unsettled_fee / product_costs / return_rate_configs 三张新表
   ——只建不存在的表，已存在则跳过。
2. 对已存在的 fact_profit_daily 用 inspect 检查后 ALTER ADD COLUMN currency / profit_kind
   （create_all 不会给已存在的表加列；利润表此前无数据，无回填负担）。

用法：
  uv run python -m scripts.migrate_phase3a_profit            # 执行
  uv run python -m scripts.migrate_phase3a_profit --dry-run  # 只打印
"""
from __future__ import annotations

import argparse

from sqlalchemy import inspect, text

from core.db import engine, init_db

# fact_profit_daily 需补的列：列名 → ALTER DDL 片段（含默认值，保证老行非空）
_PROFIT_NEW_COLUMNS = {
    "currency": "VARCHAR(8) NOT NULL DEFAULT 'CNY'",
    "profit_kind": "VARCHAR(16) NOT NULL DEFAULT 'estimated'",
}

_NEW_TABLES = ("fact_unsettled_fee", "product_costs", "return_rate_configs")


def migrate(dry_run: bool = False) -> int:
    actions: list[str] = []
    insp = inspect(engine)

    # 1) 建新表（create_all 幂等，只建不存在的）
    for t in _NEW_TABLES:
        if insp.has_table(t):
            actions.append(f"[skip] 表已存在：{t}")
        else:
            actions.append(f"CREATE TABLE {t}")
    if not dry_run:
        init_db()  # create_all：建上面缺失的表，已存在不动

    # 2) fact_profit_daily 加列（需表已存在才能 inspect 列）
    if not inspect(engine).has_table("fact_profit_daily"):
        # 表不存在 → 上面 init_db() 已按最新模型（含新列）建好，无需 ALTER
        actions.append("[info] fact_profit_daily 由 create_all 按最新模型建表（已含 currency/profit_kind）")
    else:
        existing_cols = {c["name"] for c in inspect(engine).get_columns("fact_profit_daily")}
        for col, ddl in _PROFIT_NEW_COLUMNS.items():
            if col in existing_cols:
                actions.append(f"[skip] fact_profit_daily.{col} 已存在")
                continue
            actions.append(f"ALTER TABLE fact_profit_daily ADD COLUMN {col} {ddl}")
            if not dry_run:
                with engine.begin() as conn:
                    conn.execute(
                        text(f"ALTER TABLE fact_profit_daily ADD COLUMN {col} {ddl}")
                    )

    print(("[DRY-RUN] " if dry_run else "") + "迁移动作：")
    for a in actions:
        print("  -", a)
    print("（dry-run，未实际改库）" if dry_run else "完成。")
    return 0


if __name__ == "__main__":
    from core.tenancy import TENANT_BYPASS, set_current_account

    set_current_account(TENANT_BYPASS)  # 迁移脚本需跨租户操作
    p = argparse.ArgumentParser(description="阶段3a 预估利润迁移（幂等）")
    p.add_argument("--dry-run", action="store_true", help="只打印将做什么，不改库")
    raise SystemExit(migrate(dry_run=p.parse_args().dry_run))
