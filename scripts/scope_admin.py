"""本地运维 CLI：管理业务范围 scope（business_scopes 表）。

**仅本地使用**，不起 HTTP、不对公网。创建时即校验 shop_ids 是否都在 platform_tokens
（已授权店铺）中，防止瞎配 / 配出范围外的店。

用法：
  uv run python -m scripts.scope_admin list
  uv run python -m scripts.scope_admin create \
      --key tts-id-all --name "印尼TikTok全部店" --type shop_group \
      --platform tiktok_shop --country ID --shop-ids 7494...,7495...
  uv run python -m scripts.scope_admin deactivate --key tts-id-all
"""
from __future__ import annotations

import argparse
import sys

from core.db import SessionLocal
from models.base_models import BusinessScope, PlatformToken


def _known_shop_ids(session) -> set[str]:
    rows = (
        session.query(PlatformToken.shop_id)
        .filter(PlatformToken.shop_id.isnot(None))
        .all()
    )
    return {r[0] for r in rows if r[0]}


def cmd_list(args) -> int:
    session = SessionLocal()
    try:
        q = session.query(BusinessScope)
        if getattr(args, "account_id", None):
            q = q.filter(BusinessScope.account_id == args.account_id)
        rows = q.order_by(BusinessScope.account_id, BusinessScope.scope_key).all()
        if not rows:
            print("（无 scope）")
            return 0
        for r in rows:
            flag = "" if r.is_active else " [停用]"
            print(
                f"[{r.account_id}] {r.scope_key}{flag}  | {r.scope_name} | {r.scope_type} | "
                f"platform={r.platform or '-'} country={r.country or '-'} | "
                f"shops={list(r.shop_ids or [])}"
            )
        return 0
    finally:
        session.close()


def cmd_create(args) -> int:
    shop_ids = [s.strip() for s in (args.shop_ids or "").split(",") if s.strip()]
    if args.type == "single_shop" and len(shop_ids) != 1:
        print("single_shop 必须且只能指定一个 --shop-ids", file=sys.stderr)
        return 2
    if not shop_ids:
        print("至少要指定一个 --shop-ids", file=sys.stderr)
        return 2

    session = SessionLocal()
    try:
        known = _known_shop_ids(session)
        unknown = [s for s in shop_ids if s not in known]
        if unknown:
            print(
                f"以下店铺未在 platform_tokens（已授权列表）中，拒绝创建：{unknown}",
                file=sys.stderr,
            )
            return 2

        existing = (
            session.query(BusinessScope)
            .filter(
                BusinessScope.account_id == args.account_id,
                BusinessScope.scope_key == args.key,
            )
            .first()
        )
        if existing:
            existing.scope_name = args.name
            existing.scope_type = args.type
            existing.platform = args.platform
            existing.country = args.country
            existing.shop_ids = shop_ids
            existing.is_active = True
            action = "更新"
        else:
            session.add(
                BusinessScope(
                    account_id=args.account_id,
                    scope_key=args.key,
                    scope_name=args.name,
                    scope_type=args.type,
                    platform=args.platform,
                    country=args.country,
                    shop_ids=shop_ids,
                    is_active=True,
                )
            )
            action = "创建"
        session.commit()
        print(f"已{action} scope：[{args.account_id}] {args.key}（{len(shop_ids)} 个店铺）")
        return 0
    finally:
        session.close()


def cmd_deactivate(args) -> int:
    session = SessionLocal()
    try:
        scope = (
            session.query(BusinessScope)
            .filter(
                BusinessScope.account_id == args.account_id,
                BusinessScope.scope_key == args.key,
            )
            .first()
        )
        if scope is None:
            print(f"未找到 scope：[{args.account_id}] {args.key}", file=sys.stderr)
            return 2
        scope.is_active = False
        session.commit()
        print(f"已停用 scope：{args.key}")
        return 0
    finally:
        session.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="业务范围 scope 本地管理 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出 scope（默认全部租户；--account-id 过滤）")
    p_list.add_argument(
        "--account-id", dest="account_id", default=None,
        help="只列某租户的 scope（飞书 app 维度，如 ecom-app / ecom-app-gtl）；留空列全部",
    )
    p_list.set_defaults(func=cmd_list)

    p_create = sub.add_parser("create", help="创建/更新一个 scope")
    p_create.add_argument(
        "--account-id", dest="account_id", default="ecom-app",
        help="scope 所属租户（飞书 app 维度），默认 ecom-app",
    )
    p_create.add_argument("--key", required=True, help="稳定 slug，如 tts-id-all")
    p_create.add_argument("--name", required=True, help="展示名，如 印尼TikTok全部店")
    p_create.add_argument(
        "--type", default="shop_group", choices=["single_shop", "shop_group"]
    )
    p_create.add_argument("--platform", default=None, help="如 tiktok_shop（跨平台留空）")
    p_create.add_argument("--country", default=None, help="如 ID（跨国留空）")
    p_create.add_argument("--shop-ids", dest="shop_ids", required=True, help="逗号分隔")
    p_create.set_defaults(func=cmd_create)

    p_deact = sub.add_parser("deactivate", help="停用一个 scope")
    p_deact.add_argument(
        "--account-id", dest="account_id", default="ecom-app",
        help="scope 所属租户（飞书 app 维度），默认 ecom-app",
    )
    p_deact.add_argument("--key", required=True)
    p_deact.set_defaults(func=cmd_deactivate)

    return parser


def main(argv=None) -> int:
    from core.tenancy import TENANT_BYPASS, set_current_account

    args = build_parser().parse_args(argv)
    set_current_account(getattr(args, "account_id", None) or TENANT_BYPASS)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
