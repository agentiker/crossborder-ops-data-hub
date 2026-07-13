"""一次性迁移（**仅 hp 测试环境**）：把 ecom-app-gtl 租户的店铺归并到 ecom-app，
构造「单租户多店铺」测试场景。

背景：hp 上原有两个独立租户各一个印尼 TikTok 店：
  ecom-app      → shop 7494691994496238970
  ecom-app-gtl  → shop 7494734967204644284   ← 本脚本把它归到 ecom-app 名下

为什么能只改 account_id 列：services/scoping.py 的 build_scope_key 是 "Option C"——account_id
是**隔离维度（走 account_id 列）**，故意不进 scope_key 字符串。所以业务事实数据全部原地改
account_id，scope_key 天然不冲突（各表 scope_key 含 shop_id，两店本就不同）。

决策（用户确认）：
  - 迁 token：gtl 店 token 归 ecom-app，两店都继续同步。
  - 身份类全清理：删掉 user_roles / alert_recipients 里 ecom-app-gtl 的行。
  - business_scopes：不迁 gtl 那条（会撞唯一键 account_id+scope_key='tts-id-all'），
    改为把 gtl 店 shop_id 追加进 ecom-app 现有 tts-id-all 的 shop_ids，再删 gtl 孤立 scope。
  - **审计链（api_call_logs / audit_log）单独处理，不能只盲改 account_id**：account_id 进
    哈希链 canonical → 改了它旧 row_hash 必然对不上，等同「被篡改」，verify_audit_chain 会永久
    报断裂。故改完 account_id 后须立刻 reseal_chain 用当前 canonical 重新封链（Step 4）。

幂等：重复跑安全——account_id 改列用 WHERE account_id=OLD（第二次跑命中 0 行）；
shop_ids 追加前先判存在；删除类天然幂等。

用法：
  uv run python -m scripts.migrate_merge_gtl_into_ecom_hp --dry-run   # 只打印将做什么
  uv run python -m scripts.migrate_merge_gtl_into_ecom_hp            # 实际执行（单事务）
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from core.db import SessionLocal
from services.audit import CHAIN_MODELS, record_audit_event, reseal_chain

OLD_ACCOUNT = "ecom-app-gtl"
NEW_ACCOUNT = "ecom-app"
GTL_SHOP_ID = "7494734967204644284"
ECOM_SHOP_ID = "7494691994496238970"
SCOPE_KEY = "tts-id-all"

# Step 1：直接改 account_id 的表（scope_key 不含 account，无冲突）。
# 注意：business_scopes / user_roles / alert_recipients 不在此列——各有特殊处理。
# 注意：api_call_logs / audit_log 也**不在此列**——审计链改 account_id 会断链，走 Step 4
#       （改 account_id + reseal 重新封链）。
ACCOUNT_MOVE_TABLES = [
    "platform_tokens",
    "sync_cursors",
    "inventory",
    "products",
    "sku_variants",
    "orders",
    "order_line_items",
    "pending_fulfillments",
    "raw_api_responses",
    "fact_profit_daily",
    # 以下 gtl 侧当前 0 行，但列进来保证幂等/未来有数据也能迁：
    "fact_ad_spend_daily",
    "fact_gmv_max_spend_daily",
    "fact_finance_transaction",
    "fact_unsettled_fee",
    "fact_alerts",
    "super_hot_products",
    "product_costs",
    "return_rate_configs",
    "replenishment_config",
    "fulfillment_alert_state",
    "stock_alert_state",
    "fee_rate_alert_state",
    "hotsell_alert_state",
    "biz_configs",
    "conversation_scope_bindings",
]


def _count(session, table: str, account: str) -> int:
    return session.execute(
        text(f"SELECT COUNT(*) FROM {table} WHERE account_id=:a"), {"a": account}
    ).scalar()


def run(dry_run: bool) -> None:
    session = SessionLocal()
    try:
        print(f"=== 合并 {OLD_ACCOUNT} → {NEW_ACCOUNT}（dry_run={dry_run}）===\n")

        # ---- Step 1：改 account_id ----
        print("── Step 1：业务数据 + token 改 account_id ──")
        total_moved = 0
        for t in ACCOUNT_MOVE_TABLES:
            try:
                n = _count(session, t, OLD_ACCOUNT)
            except Exception as exc:  # 表不存在等，跳过并提示
                print(f"  {t:28} 跳过（{str(exc)[:40]}）")
                continue
            if n == 0:
                continue
            total_moved += n
            if dry_run:
                print(f"  {t:28} 将迁 {n} 行")
            else:
                session.execute(
                    text(f"UPDATE {t} SET account_id=:new WHERE account_id=:old"),
                    {"new": NEW_ACCOUNT, "old": OLD_ACCOUNT},
                )
                print(f"  {t:28} 已迁 {n} 行")
        print(f"  小计：{total_moved} 行\n")

        # ---- Step 2：business_scopes 合并 shop_ids ----
        print("── Step 2：business_scopes 合并 shop_ids ──")
        row = session.execute(
            text("SELECT shop_ids FROM business_scopes WHERE account_id=:a AND scope_key=:k"),
            {"a": NEW_ACCOUNT, "k": SCOPE_KEY},
        ).fetchone()
        if row is None:
            print(f"  !! 未找到 {NEW_ACCOUNT}/{SCOPE_KEY}，跳过（请人工检查）")
        else:
            import json

            shop_ids = row[0] if isinstance(row[0], list) else json.loads(row[0] or "[]")
            if GTL_SHOP_ID in shop_ids:
                print(f"  {NEW_ACCOUNT}/{SCOPE_KEY} 已含 gtl 店，无需追加：{shop_ids}")
            else:
                new_ids = shop_ids + [GTL_SHOP_ID]
                print(f"  {NEW_ACCOUNT}/{SCOPE_KEY} shop_ids: {shop_ids} → {new_ids}")
                if not dry_run:
                    session.execute(
                        text("UPDATE business_scopes SET shop_ids=:s WHERE account_id=:a AND scope_key=:k"),
                        {"s": json.dumps(new_ids), "a": NEW_ACCOUNT, "k": SCOPE_KEY},
                    )
        # 删 gtl 孤立 scope
        gtl_scope_n = session.execute(
            text("SELECT COUNT(*) FROM business_scopes WHERE account_id=:a"), {"a": OLD_ACCOUNT}
        ).scalar()
        if gtl_scope_n:
            print(f"  删除 {OLD_ACCOUNT} 的孤立 scope：{gtl_scope_n} 行")
            if not dry_run:
                session.execute(
                    text("DELETE FROM business_scopes WHERE account_id=:a"), {"a": OLD_ACCOUNT}
                )
        print()

        # ---- Step 3：身份类清理 ----
        print("── Step 3：清理 gtl 身份（user_roles / alert_recipients）──")
        for t in ("user_roles", "alert_recipients"):
            n = _count(session, t, OLD_ACCOUNT)
            if n:
                print(f"  {t:20} 删除 {n} 行")
                if not dry_run:
                    session.execute(
                        text(f"DELETE FROM {t} WHERE account_id=:a"), {"a": OLD_ACCOUNT}
                    )
        print()

        # ---- Step 4：审计链迁移（改 account_id + 重新封链）----
        # account_id 进哈希链 canonical，盲改会断链。故此处「改 account_id + reseal」绑在一起：
        # 先把 gtl 审计行归到 ecom-app，再用当前 canonical 按 id 重算 ecom-app 整条链，令其自洽。
        print("── Step 4：审计链迁移 + 重新封链（api_call_logs / audit_log）──")
        for model, canonical_fn in CHAIN_MODELS:
            t = model.__tablename__
            n = _count(session, t, OLD_ACCOUNT)
            print(f"  {t:16} gtl 侧 {n} 行归入 {NEW_ACCOUNT}", end="")
            if dry_run:
                print(f"，并将重新封链 {NEW_ACCOUNT} 整条链（未执行）")
                continue
            if n:
                session.execute(
                    text(f"UPDATE {t} SET account_id=:new WHERE account_id=:old"),
                    {"new": NEW_ACCOUNT, "old": OLD_ACCOUNT},
                )
            resealed = reseal_chain(session, model, canonical_fn, NEW_ACCOUNT)
            print(f"，已重封 {resealed} 行")
        if not dry_run:
            # 重封留痕：追加到刚重封好的 audit_log 链尾，自洽。
            record_audit_event(
                session,
                event_type="audit_maintenance",
                event_action="reseal_chain",
                actor_source="migration",
                target=NEW_ACCOUNT,
                summary=f"并租户 {OLD_ACCOUNT}→{NEW_ACCOUNT} 后重新封链",
                account_id=NEW_ACCOUNT,
            )
        print()

        if dry_run:
            session.rollback()
            print("=== dry-run 结束，未提交 ===")
        else:
            session.commit()
            print("=== 已提交 ===")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印将做什么，不提交")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
