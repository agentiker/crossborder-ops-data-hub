"""Phase 3b 迁移：对齐 platform_tokens.scope_key 的 account 段到 account_id 列。**幂等**。

Phase 3 迁移（migrate_phase3_scope_account.py）回填了 platform_tokens.account_id='ecom-app'，
但**没更新 scope_key 字符串**（仍含 `account=_`）。discover_single_shop 读 account_id 列、
让 client 重建 scope_key 得 `account=ecom-app`，与存量 `account=_` 不一致 →
load_existing_token 的 filter_by(scope_key=...) 查不到 token → 报
"Token已过期且无refresh_token"（2026-06-21 生产 sync 全挂的真根因，token 本身完好）。

本脚本用 build_scope_key 按每行实际列值（含 account_id）重算 scope_key，与当前不一致则对齐。
只改 platform_tokens（sync 阻断点）；cursor/raw 的 account=_ 存量不迁——cursor 不一致仅导致
一次重新同步（无害），raw 是历史留档。幂等：已一致的行跳过，可重复跑。

用法：
  uv run python -m scripts.migrate_phase3b_token_scope_key --dry-run
  uv run python -m scripts.migrate_phase3b_token_scope_key
"""
from __future__ import annotations

import argparse

from core.db import SessionLocal, init_db
from models.base_models import PlatformToken
from services.scoping import build_scope_key


def migrate(dry_run: bool = False) -> int:
    init_db()
    session = SessionLocal()
    try:
        rows = session.query(PlatformToken).all()
        changed = 0
        for t in rows:
            expected = build_scope_key(
                platform=t.platform,
                country=t.country or "GLOBAL",
                shop_id=t.shop_id,
                seller_id=t.seller_id,
                account_id=t.account_id,
            )
            if t.scope_key != expected:
                print(f"对齐: {t.scope_key!r}\n   -> {expected!r}")
                if not dry_run:
                    t.scope_key = expected
                changed += 1
            else:
                print(f"[skip] 已一致: {t.scope_key!r}")
        if not dry_run:
            session.commit()
        print(f"\n{'(dry-run) ' if dry_run else ''}需对齐 {changed} 行，共 {len(rows)} 行 platform_tokens")
        return changed
    finally:
        session.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Phase 3b: 对齐 platform_tokens.scope_key account 段（幂等）")
    p.add_argument("--dry-run", action="store_true", help="只打印不写库")
    args = p.parse_args()
    migrate(dry_run=args.dry_run)
