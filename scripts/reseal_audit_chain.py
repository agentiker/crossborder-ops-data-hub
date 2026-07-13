"""审计哈希链**重新封链** CLI（plan 审计合规第 2 节的维护配套）。

**用途 = 合法管理操作之后重新封链**：某些授权操作（典型是租户合并，UPDATE account_id）会改动
被哈希的字段，从审计链角度等同「行被篡改」→ verify_audit_chain 从此永久报断裂。本工具按 id
顺序用当前 canonical 重算指定租户整条链的 prev_hash+row_hash 写回，使链重新自洽、verify 通过，
并记一条 AuditLog 元事件（event_type=audit_maintenance）说明重封缘由，令「重封」本身可追溯。

⚠️ 这是对不可篡改日志的授权重写，只应在确认过的管理操作后执行。默认要求显式 --account，
不提供 --all 之外的隐式全库重封，避免误伤。

用法：
  uv run python -m scripts.reseal_audit_chain --account ecom-app --reason "并租户 gtl→ecom-app" --dry-run
  uv run python -m scripts.reseal_audit_chain --account ecom-app --reason "并租户 gtl→ecom-app"
"""
from __future__ import annotations

import argparse
import sys

from core.db import SessionLocal
from core.tenancy import TENANT_BYPASS, set_current_account
from services.audit import (
    CHAIN_MODELS,
    record_audit_event,
    reseal_chain,
    verify_chain,
)


def run(account: str, reason: str, dry_run: bool) -> int:
    # 跨租户读写：绕过 ORM 自动租户过滤，否则只看得到当前租户链。
    set_current_account(TENANT_BYPASS)
    session = SessionLocal()
    try:
        print(f"=== 重新封链 account={account}（dry_run={dry_run}）===")
        total_rows = 0
        total_breaks_before = 0
        for model, canonical_fn in CHAIN_MODELS:
            t = model.__tablename__
            # 只统计本 account 的断裂数（verify_chain 返回全表，过滤本 account）。
            breaks_before = [b for b in verify_chain(session, model, canonical_fn)
                             if b["account_id"] == account]
            n = (session.query(model).filter(model.account_id == account).count())
            total_rows += n
            total_breaks_before += len(breaks_before)
            print(f"  {t:16} 本 account {n} 行，重封前断裂 {len(breaks_before)} 行")
            if not dry_run and n:
                reseal_chain(session, model, canonical_fn, account)

        if dry_run:
            session.rollback()
            print(f"=== dry-run 结束：将重封 {total_rows} 行（当前断裂 {total_breaks_before}），未提交 ===")
            return 0

        # 重封后记一条元事件留痕（追加到刚重封好的 audit_log 链尾，自洽）。
        record_audit_event(
            session,
            event_type="audit_maintenance",
            event_action="reseal_chain",
            actor_source="cli",
            target=account,
            summary=f"重新封链({total_rows}行/原断裂{total_breaks_before}): {reason}"[:500],
        )
        session.commit()
        print(f"=== 已重封 {total_rows} 行并留痕，已提交 ===")

        # 提交后复验，确认本 account 已无断裂。
        remaining = [b for model, fn in CHAIN_MODELS
                     for b in verify_chain(session, model, fn)
                     if b["account_id"] == account]
        if remaining:
            print(f"✗ 复验仍有 {len(remaining)} 行断裂：{remaining[:10]}")
            return 1
        print("✓ 复验通过：本 account 两条链完好。")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="审计哈希链重新封链（授权管理操作后使用）")
    p.add_argument("--account", required=True, help="要重新封链的 account_id")
    p.add_argument("--reason", required=True, help="重封缘由（写入 AuditLog 元事件留痕）")
    p.add_argument("--dry-run", action="store_true", help="只统计将重封多少行，不写库")
    args = p.parse_args()
    try:
        raise SystemExit(run(args.account, args.reason, args.dry_run))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 重封运行异常: {exc}", file=sys.stderr)
        raise SystemExit(2)
