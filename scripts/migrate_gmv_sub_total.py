"""迁移：orders 表加 sub_total 列（商品小计，payment.sub_total）。**幂等**，可重复跑。

展示类 GMV 改用 sub_total 对齐 TikTok 后台口径（含所有状态、不含运费税）。orders 表已存在、
有数据，create_all 不会给已存在的表加列，故手写 ALTER。列**允许 NULL**（不设默认值）——回填前
老单为 NULL，聚合侧 coalesce(sub_total, total_amount) 兜底；回填靠重跑 sync_orders 幂等 update。

用法：
  uv run python -m scripts.migrate_gmv_sub_total            # 执行
  uv run python -m scripts.migrate_gmv_sub_total --dry-run  # 只打印

⚠️ 部署顺序：先跑本迁移加列 → 立即跑 `uv run python -m flows.sync_orders --since-days 30`
回填老单 sub_total → 再验证展示 GMV 对齐后台。回填前展示 GMV 靠 coalesce 退回 total_amount（略偏高），不会暴跌到 0。
"""
from __future__ import annotations

import argparse

from sqlalchemy import inspect, text

from core.db import engine


def migrate(dry_run: bool = False) -> int:
    actions: list[str] = []
    insp = inspect(engine)

    if not insp.has_table("orders"):
        # 表不存在 → create_all（init_db）会按最新模型建表，已含 sub_total，无需 ALTER
        actions.append("[info] orders 表不存在，由 init_db/create_all 按最新模型建表（已含 sub_total）")
    else:
        existing_cols = {c["name"] for c in insp.get_columns("orders")}
        if "sub_total" in existing_cols:
            actions.append("[skip] orders.sub_total 已存在")
        else:
            ddl = "ALTER TABLE orders ADD COLUMN sub_total NUMERIC(18, 4) NULL"
            actions.append(ddl)
            if not dry_run:
                with engine.begin() as conn:
                    conn.execute(text(ddl))

    print(("[DRY-RUN] " if dry_run else "") + "迁移动作：")
    for a in actions:
        print("  -", a)
    print("（dry-run，未实际改库）" if dry_run else "完成。回填：uv run python -m flows.sync_orders --since-days 30")
    return 0


if __name__ == "__main__":
    from core.tenancy import TENANT_BYPASS, set_current_account

    set_current_account(TENANT_BYPASS)  # 迁移脚本需跨租户操作
    p = argparse.ArgumentParser(description="orders 加 sub_total 列（幂等）")
    p.add_argument("--dry-run", action="store_true", help="只打印将做什么，不改库")
    raise SystemExit(migrate(dry_run=p.parse_args().dry_run))
