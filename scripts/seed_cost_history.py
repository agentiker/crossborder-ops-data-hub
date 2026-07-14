"""一次性 seed：把现有 `product_costs` 当前快照写入 `product_cost_history`（effective_from=业务今日）。

让 `get_cost_map_asof` 立即有历史数据可查（否则历史表为空只能回落当前快照）。**幂等**：
复用 record_cost_history 的 append-on-change——重复跑同日同值不产生重复行。之后靠马帮周更
flow（import_costs_from_rows 内已调 record_cost_history）持续 append-on-change 累积。

用法：
  uv run python -m scripts.seed_cost_history            # 执行
  uv run python -m scripts.seed_cost_history --dry-run  # 只统计将写入多少,不改库
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from core.db import SessionLocal
from core.timezone import business_today
from models.base_models import ProductCost
from services.product_cost_store import record_cost_history


def seed(dry_run: bool = False) -> int:
    eff = business_today()
    session = SessionLocal()
    try:
        rows = session.query(
            ProductCost.account_id, ProductCost.platform,
            ProductCost.seller_sku, ProductCost.unit_cost_rmb, ProductCost.note,
        ).all()
        # 按 (account_id, platform) 分组
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for account_id, platform, sku, cost, note in rows:
            groups[(account_id, platform)].append(
                {"seller_sku": sku, "unit_cost_rmb": cost, "note": note}
            )

        print(("[DRY-RUN] " if dry_run else "") + f"seed 成本历史 effective_from={eff}")
        total_added = total_unchanged = 0
        for (account_id, platform), grp in sorted(groups.items(), key=lambda x: str(x[0])):
            res = record_cost_history(
                session, grp, account_id=account_id, platform=platform, effective_from=eff,
            )
            total_added += res["added"]
            total_unchanged += res["unchanged"] + res["updated"]
            print(f"  {account_id}/{platform}: 源 {len(grp)} 条 → "
                  f"新增 {res['added']}、已存在 {res['unchanged'] + res['updated']}")

        if dry_run:
            session.rollback()
            print(f"（dry-run，未实际改库）合计将新增 ~{total_added} 行历史")
        else:
            session.commit()
            print(f"完成。新增 {total_added} 行、跳过 {total_unchanged} 行。")
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    from core.tenancy import TENANT_BYPASS, set_current_account

    set_current_account(TENANT_BYPASS)  # 跨租户遍历全部 product_costs
    p = argparse.ArgumentParser(description="seed product_cost_history（幂等）")
    p.add_argument("--dry-run", action="store_true", help="只统计，不改库")
    raise SystemExit(seed(dry_run=p.parse_args().dry_run))
