"""本地运维 CLI：管理用户角色与权限上限（user_roles 表，plan/14 方案 B）。

**仅本地使用**，不起 HTTP、不对公网。是统一权限闸 services/user_authz 的真相录入口。
boss 看全部；operator 被钉死在 --scope-key 且不可越界。operator 必须给 --scope-key
且校验该 scope 存在且 active（复用 scope_resolution.expand_scope）；boss 忽略 scope。

⚠️ 上线防自锁（对话侧登记闸 plan/09 Phase 7）：置 settings.feishu_oauth.enforce_dialog_authz
=True 前，先确认 boss/operator 都已登记齐（首登者经 OAuth 自助 bootstrap 成 boss，本 CLI 用于
补登/改角色/纠错），否则一开硬闸登录态以外的对话路径会把未登记者全部挡在外面。

用法：
  uv run python -m scripts.user_admin list
  uv run python -m scripts.user_admin set --open-id ou_boss --role boss --note 老板
  uv run python -m scripts.user_admin set --open-id ou_op --role operator \
      --scope-key tts-id-all --note 运营A
  uv run python -m scripts.user_admin deactivate --open-id ou_op
"""
from __future__ import annotations

import argparse
import sys

from core.db import SessionLocal
from models.base_models import UserRole
from services.scope_resolution import ScopeError, expand_scope


def cmd_list(args) -> int:
    session = SessionLocal()
    try:
        rows = (
            session.query(UserRole)
            .order_by(UserRole.account_id, UserRole.role, UserRole.open_id)
            .all()
        )
        if not rows:
            print("（无用户角色）")
            return 0
        for r in rows:
            flag = "" if r.is_active else " [停用]"
            scope = r.allowed_scope_key or "-"
            if r.role == "boss":
                scope = "全部"
            note = f" | {r.note}" if r.note else ""
            print(
                f"{r.open_id}{flag}  | {r.role} | scope={scope} | "
                f"{r.channel}/{r.account_id}{note}"
            )
        return 0
    finally:
        session.close()


def cmd_set(args) -> int:
    role = args.role
    scope_key = (args.scope_key or "").strip() or None

    if role == "operator":
        if not scope_key:
            print("operator 必须指定 --scope-key（不可越界的硬上限）", file=sys.stderr)
            return 2
        # 校验：未知/停用 scope 直接拒，绝不落脏权限。多租户：只在该用户租户内找 scope。
        try:
            expand_scope(scope_key, account_id=args.account_id)
        except ScopeError as e:
            print(f"scope 校验失败：{e}", file=sys.stderr)
            return 2
    else:  # boss 忽略 scope（看全部）
        if scope_key:
            print("boss 看全部，已忽略 --scope-key", file=sys.stderr)
        scope_key = None

    session = SessionLocal()
    try:
        row = (
            session.query(UserRole)
            .filter(
                UserRole.channel == args.channel,
                UserRole.account_id == args.account_id,
                UserRole.open_id == args.open_id,
            )
            .first()
        )
        if row is None:
            row = UserRole(
                channel=args.channel,
                account_id=args.account_id,
                open_id=args.open_id,
                role=role,
                allowed_scope_key=scope_key,
                note=args.note,
                is_active=True,
            )
            session.add(row)
            action = "创建"
        else:
            row.role = role
            row.allowed_scope_key = scope_key
            if args.note is not None:
                row.note = args.note
            row.is_active = True
            action = "更新"
        session.commit()
        scope_display = "全部" if role == "boss" else scope_key
        print(f"已{action}用户角色：{args.open_id}（{role}，scope={scope_display}）")
        return 0
    finally:
        session.close()


def cmd_deactivate(args) -> int:
    session = SessionLocal()
    try:
        row = (
            session.query(UserRole)
            .filter(
                UserRole.channel == args.channel,
                UserRole.account_id == args.account_id,
                UserRole.open_id == args.open_id,
            )
            .first()
        )
        if row is None:
            print(f"未找到用户角色：{args.open_id}", file=sys.stderr)
            return 2
        row.is_active = False
        session.commit()
        print(f"已停用用户角色：{args.open_id}")
        return 0
    finally:
        session.close()


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--open-id", dest="open_id", required=True, help="飞书用户 ou_xxx")
    p.add_argument("--channel", default="feishu")
    p.add_argument(
        "--account-id", dest="account_id", default="ecom-app",
        help="飞书 app 标识（open_id 是 per-app 的，须与对话/OAuth 同一 app）",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="用户角色与权限上限本地管理 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出所有用户角色").set_defaults(func=cmd_list)

    p_set = sub.add_parser("set", help="创建/更新一个用户角色（upsert）")
    _add_common(p_set)
    p_set.add_argument("--role", required=True, choices=["boss", "operator"])
    p_set.add_argument(
        "--scope-key", dest="scope_key", default=None,
        help="operator 的硬上限 scope（必填且须 active）；boss 忽略",
    )
    p_set.add_argument("--note", default=None, help="备注，如姓名/岗位")
    p_set.set_defaults(func=cmd_set)

    p_deact = sub.add_parser("deactivate", help="停用一个用户角色")
    _add_common(p_deact)
    p_deact.set_defaults(func=cmd_deactivate)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
