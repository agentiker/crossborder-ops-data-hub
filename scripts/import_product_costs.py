"""CLI 导入产品成本 CSV（兜底入口，业务逻辑同 admin 端点 /api/admin/product-costs/import）。

CSV 列：seller_sku,unit_cost_rmb[,note]（首行表头，RMB 含运费）。
用法：
  uv run python -m scripts.import_product_costs <csv_path> --account ecom-app [--platform tiktok_shop]
"""
from __future__ import annotations

import argparse
import csv
import os

from core.db import SessionLocal
from core.tenancy import set_current_account
from services.audit import record_audit_event_safe
from services.product_cost_store import import_costs_from_rows


def main() -> int:
    from core.audit_context import set_audit_actor

    p = argparse.ArgumentParser(description="导入产品成本 CSV（RMB 含运费）")
    p.add_argument("csv_path", help="CSV 路径（列 seller_sku,unit_cost_rmb[,note]）")
    p.add_argument("--account", required=True, help="account_id，如 ecom-app")
    p.add_argument("--platform", default="tiktok_shop")
    args = p.parse_args()

    set_current_account(args.account)
    set_audit_actor(open_id=os.getenv("USER"), source="cli")  # CLI 审计身份
    with open(args.csv_path, encoding="utf-8-sig") as f:
        rows = [dict(r) for r in csv.DictReader(f)]
    session = SessionLocal()
    try:
        result = import_costs_from_rows(
            session, rows, account_id=args.account, platform=args.platform
        )
        session.commit()
        record_audit_event_safe(
            session,
            event_type="account_op", event_action="product_costs.import",
            actor_open_id=os.getenv("USER"), actor_source="cli", account_id=args.account,
            target=args.csv_path, summary=f"CLI 导入产品成本 CSV：{args.csv_path}",
            after=result,
        )
    finally:
        session.close()
    print(f"导入完成：新增 {result['inserted']}、更新 {result['updated']}、错误 {len(result['errors'])}")
    for e in result["errors"]:
        print(f"  [行 {e['row']}] {e['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
