"""Option C 迁移：把 account 段从 key 字符串里彻底去掉，隔离纯靠 account_id 列。**幂等**。

背景：历史上 account 被双写——既进 account_id 列、又拼进 scope_key / idempotency_key
字符串（`...|account=ecom-app|...`）。这造成 orders 同步报
`Duplicate entry ... for key 'orders.uq_order_scope_order_id'`（idempotency_key 含 account
段 ≠ 唯一约束不含 account，判重落空 → INSERT → 撞唯一键）。Option C 让 build_scope_key
家族不再把 account 入串（见 services/scoping.py），隔离 100% 靠 account_id 列。

本脚本对齐存量数据：

  Phase 1 — platform_tokens：**绝不截断**（刚重新授权过的 token 在里面）。逐行用新版
            build_scope_key 按行自身列值重算 scope_key（去 account），仅不一致才更新，
            保留所有 token 字段 + account_id 列。commit 前断言重算后集合唯一。

  Phase 2 — 截断可从 TikTok 重新流入的事实/游标/派生表。**为何截断而非原地重算**：
            inventory/products 很可能同时存在 account=_ 与 account=ecom-app 的重复行
            （改 token 前后各同步一次，表 uq 含 account_id 故两行都入库），原地去 account
            会撞 idempotency_key unique。截断绕过一切，数据从 TikTok 重新流入，各 store
            重写 account_id 列。alert state 去重表也截断（其 state_key 走 build_alert_state_key
            仍保留 account，但截空只是重置去重游标，无害）。

  **不截断**：platform_tokens（Phase 1）+ 租户/管理表（business_scopes / user_roles /
            conversation_scope_bindings / alert_recipients / web_conversations / web_messages）。

用法：
  uv run python -m scripts.migrate_option_c_scope_keys --dry-run     # 只打印不写库
  uv run python -m scripts.migrate_option_c_scope_keys --skip-truncate  # 仅重对齐 token
  uv run python -m scripts.migrate_option_c_scope_keys              # 全量执行
"""
from __future__ import annotations

import argparse

from sqlalchemy import text

from core.db import SessionLocal, init_db
from models.base_models import (
    Alert,
    DailyProfit,
    FactAdSpendDaily,
    FulfillmentAlertState,
    Inventory,
    OrderHeader,
    OrderLineItem,
    PendingFulfillment,
    PlatformToken,
    Product,
    RawAPIResponse,
    StockAlertState,
    SyncCursor,
)
from services.scoping import build_scope_key

# 可从 TikTok 重新同步 / 可重算的派生表——截断后由各 store 重灌（含 account_id 列）。
TRUNCATE_MODELS = [
    OrderHeader,
    OrderLineItem,
    PendingFulfillment,
    Inventory,
    Product,
    SyncCursor,
    RawAPIResponse,
    DailyProfit,
    FactAdSpendDaily,
    Alert,
    FulfillmentAlertState,
    StockAlertState,
]


def _realign_tokens(session, dry_run: bool) -> int:
    """Phase 1：原地重算 platform_tokens.scope_key（去 account），保留凭据。"""
    rows = session.query(PlatformToken).all()
    changed = 0
    expected_keys: list[str] = []
    for t in rows:
        expected = build_scope_key(
            platform=t.platform,
            country=t.country or "GLOBAL",
            shop_id=t.shop_id,
            seller_id=t.seller_id,
            account_id=t.account_id,  # 被忽略，仅为兼容；保留以示意 account 不入串
        )
        expected_keys.append(expected)
        if t.scope_key != expected:
            print(f"[token] {t.scope_key!r}\n     -> {expected!r}")
            if not dry_run:
                t.scope_key = expected
            changed += 1
        else:
            print(f"[token][skip] 已一致: {t.scope_key!r}")

    # 碰撞守卫：重算后的 scope_key 必须仍唯一，否则会违反 platform_tokens.scope_key unique。
    if len(expected_keys) != len(set(expected_keys)):
        raise RuntimeError(
            f"重算后 platform_tokens.scope_key 出现重复，拒绝提交：{expected_keys}"
        )
    print(f"\nPhase 1: 需对齐 {changed} 行，共 {len(rows)} 行 platform_tokens")
    return changed


def _truncate_fact_tables(session, dry_run: bool) -> None:
    """Phase 2：截断派生/事实/游标表，数据从 TikTok 重新流入。"""
    dialect = session.get_bind().dialect.name
    print(f"\nPhase 2: 截断派生表（dialect={dialect}）")

    if dialect == "mysql" and not dry_run:
        session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    try:
        for model in TRUNCATE_MODELS:
            tbl = model.__tablename__
            n = session.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar()
            print(f"[truncate] {tbl}: {n} 行")
            if dry_run:
                continue
            if dialect == "mysql":
                session.execute(text(f"TRUNCATE TABLE {tbl}"))
            else:  # sqlite 等不支持 TRUNCATE
                session.execute(text(f"DELETE FROM {tbl}"))
    finally:
        if dialect == "mysql" and not dry_run:
            session.execute(text("SET FOREIGN_KEY_CHECKS=1"))


def migrate(dry_run: bool = False, skip_truncate: bool = False) -> None:
    init_db()
    session = SessionLocal()
    try:
        _realign_tokens(session, dry_run)
        if not skip_truncate:
            _truncate_fact_tables(session, dry_run)
        else:
            print("\nPhase 2: --skip-truncate，跳过截断")

        if not dry_run:
            session.commit()
            print("\n已提交。")
        else:
            print("\n(dry-run) 未写库。")
    finally:
        session.close()

    if not skip_truncate and not dry_run:
        print(
            "\n⚠️  截断后派生表为空。务必立即运行同步重灌数据：\n"
            "    uv run python -m flows.sync_orders\n"
            "    uv run python -m flows.sync_inventory\n"
            "    uv run python -m flows.sync_fulfillments\n"
            "    （否则看板/报告无数据）"
        )


if __name__ == "__main__":
    from core.tenancy import TENANT_BYPASS, set_current_account

    set_current_account(TENANT_BYPASS)  # 迁移脚本需跨租户操作
    p = argparse.ArgumentParser(
        description="Option C: 去掉 key 字符串的 account 段，隔离纯靠 account_id 列（幂等）"
    )
    p.add_argument("--dry-run", action="store_true", help="只打印不写库")
    p.add_argument(
        "--skip-truncate",
        action="store_true",
        help="仅重对齐 platform_tokens.scope_key，不截断派生表",
    )
    args = p.parse_args()
    migrate(dry_run=args.dry_run, skip_truncate=args.skip_truncate)
