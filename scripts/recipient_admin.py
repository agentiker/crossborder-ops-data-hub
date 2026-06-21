"""本地运维 CLI：管理告警收件人（alert_recipients 表）。

**仅本地使用**。收件人 = 某租户(account)下某飞书用户(open_id)收某范围(scope_key)的告警。
scope_key 留空 = 本租户全量范围。add 时若 scope_key 非空会校验它在该租户内存在且 active。

用法：
  uv run python -m scripts.recipient_admin list [--account-id ecom-app-gtl]
  uv run python -m scripts.recipient_admin add --account-id ecom-app-gtl \
      --open-id ou_xxx [--scope-key tts-id-all] [--note 张三]
  uv run python -m scripts.recipient_admin deactivate --account-id ecom-app-gtl --open-id ou_xxx
"""
from __future__ import annotations

import argparse
import sys

from core.db import SessionLocal
from models.base_models import AlertRecipient
from services.scope_resolution import ScopeError, expand_scope


def cmd_list(args) -> int:
    session = SessionLocal()
    try:
        q = session.query(AlertRecipient)
        if getattr(args, "account_id", None):
            q = q.filter(AlertRecipient.account_id == args.account_id)
        rows = q.order_by(AlertRecipient.account_id, AlertRecipient.open_id).all()
        if not rows:
            print("（无收件人）")
            return 0
        for r in rows:
            flag = "" if r.is_active else " [停用]"
            note = f" | {r.note}" if r.note else ""
            print(f"[{r.account_id}] {r.open_id}{flag} | scope={r.scope_key or '全量'} | "
                  f"{r.channel}{note}")
        return 0
    finally:
        session.close()


def cmd_add(args) -> int:
    scope_key = (args.scope_key or "").strip() or None
    if scope_key is not None:
        # 校验 scope 在该租户内存在且 active（绝不落脏收件人）。
        try:
            expand_scope(scope_key, account_id=args.account_id)
        except ScopeError as e:
            print(f"scope 校验失败：{e}", file=sys.stderr)
            return 2

    session = SessionLocal()
    try:
        row = (
            session.query(AlertRecipient)
            .filter(
                AlertRecipient.channel == "feishu",
                AlertRecipient.account_id == args.account_id,
                AlertRecipient.open_id == args.open_id,
            )
            .first()
        )
        if row:
            row.scope_key = scope_key
            row.is_active = True
            if args.note is not None:
                row.note = args.note
            action = "更新"
        else:
            session.add(AlertRecipient(
                channel="feishu", account_id=args.account_id, open_id=args.open_id,
                scope_key=scope_key, note=args.note, is_active=True,
            ))
            action = "创建"
        session.commit()
        print(f"已{action}收件人：[{args.account_id}] {args.open_id}（scope={scope_key or '全量'}）")
        return 0
    finally:
        session.close()


def cmd_deactivate(args) -> int:
    session = SessionLocal()
    try:
        row = (
            session.query(AlertRecipient)
            .filter(
                AlertRecipient.channel == "feishu",
                AlertRecipient.account_id == args.account_id,
                AlertRecipient.open_id == args.open_id,
            )
            .first()
        )
        if row is None:
            print(f"未找到收件人：[{args.account_id}] {args.open_id}", file=sys.stderr)
            return 2
        row.is_active = False
        session.commit()
        print(f"已停用收件人：[{args.account_id}] {args.open_id}")
        return 0
    finally:
        session.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="告警收件人本地管理 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出收件人")
    p_list.add_argument("--account-id", dest="account_id", default=None, help="只列某租户")
    p_list.set_defaults(func=cmd_list)

    p_add = sub.add_parser("add", help="新增/更新一个收件人")
    p_add.add_argument("--account-id", dest="account_id", required=True)
    p_add.add_argument("--open-id", dest="open_id", required=True)
    p_add.add_argument("--scope-key", dest="scope_key", default=None, help="留空=本租户全量")
    p_add.add_argument("--note", dest="note", default=None)
    p_add.set_defaults(func=cmd_add)

    p_deact = sub.add_parser("deactivate", help="停用一个收件人")
    p_deact.add_argument("--account-id", dest="account_id", required=True)
    p_deact.add_argument("--open-id", dest="open_id", required=True)
    p_deact.set_defaults(func=cmd_deactivate)

    return parser


def main(argv=None) -> int:
    from core.tenancy import TENANT_BYPASS, set_current_account

    args = build_parser().parse_args(argv)
    set_current_account(getattr(args, "account_id", None) or TENANT_BYPASS)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
