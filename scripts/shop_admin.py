"""本地运维 CLI：管理 TikTok 店铺的租户归属（platform_tokens.account_id）。

**仅本地使用**。同一个 TikTok app 授权多店时，OAuth 回调暂存到 DEFAULT_ACCOUNT
(ecom-app)；新店若属别的租户（如 ecom-app-gtl），授权后用 `assign` 把归属设对。

⚠️ 加非主租户的店：授权 → **立即 assign** → 再等 sync。assign 前若 sync 跑了，
该店会被当 DEFAULT_ACCOUNT 同步一次（数据落错租户，需后续清理/重同步）。

用法：
  uv run python -m scripts.shop_admin list
  uv run python -m scripts.shop_admin assign --shop-id 7494... --account-id ecom-app-gtl
"""
from __future__ import annotations

import argparse
import sys

from core.db import SessionLocal
from core.tenancy import is_valid_account
from models.base_models import PlatformToken


def cmd_list(args) -> int:
    session = SessionLocal()
    try:
        rows = (
            session.query(PlatformToken)
            .filter(PlatformToken.shop_id.isnot(None))
            .order_by(PlatformToken.account_id, PlatformToken.shop_id)
            .all()
        )
        if not rows:
            print("（platform_tokens 无授权店铺）")
            return 0
        for r in rows:
            print(f"[{r.account_id}] shop={r.shop_id} country={r.country} "
                  f"seller={r.seller_id or '-'} | scope_key={r.scope_key}")
        return 0
    finally:
        session.close()


def cmd_assign(args) -> int:
    if not is_valid_account(args.account_id):
        print(f"非法 account_id：{args.account_id}（不在已知租户集；检查 TENANCY__HOST_TO_ACCOUNT）",
              file=sys.stderr)
        return 2

    session = SessionLocal()
    try:
        rows = (
            session.query(PlatformToken)
            .filter(PlatformToken.shop_id == args.shop_id)
            .all()
        )
        if not rows:
            print(f"未找到 shop_id={args.shop_id} 的 token（先走 OAuth 授权）", file=sys.stderr)
            return 2
        changed = []
        for r in rows:
            if r.account_id != args.account_id:
                changed.append((r.scope_key, r.account_id))
                r.account_id = args.account_id
        session.commit()
        if not changed:
            print(f"shop_id={args.shop_id} 已归属 {args.account_id}，无需变更")
        else:
            for scope_key, old in changed:
                print(f"已改归属：shop={args.shop_id} {old} → {args.account_id}（{scope_key}）")
        return 0
    finally:
        session.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TikTok 店铺租户归属本地管理 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出所有授权店铺及其租户归属")
    p_list.set_defaults(func=cmd_list)

    p_assign = sub.add_parser("assign", help="设置某店铺的租户归属")
    p_assign.add_argument("--shop-id", dest="shop_id", required=True)
    p_assign.add_argument("--account-id", dest="account_id", required=True)
    p_assign.set_defaults(func=cmd_assign)

    return parser


def main(argv=None) -> int:
    # 跨租户操作：查/改任意租户的 token 行，须 BYPASS 绕过 ORM 自动租户过滤。
    from core.tenancy import TENANT_BYPASS, set_current_account

    args = build_parser().parse_args(argv)
    set_current_account(TENANT_BYPASS)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
