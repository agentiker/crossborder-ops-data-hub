"""存量 token 明文 → 密文迁移（plan 审计合规第 5 节）。**幂等**，可重复跑。

platform_tokens.access_token/refresh_token 列已改 EncryptedText（透明加解密），但**存量行
仍是明文**。本脚本把存量明文就地加密：用 raw SQL 读**原始**列值（绕过 ORM 解密，才能凭
fcr1: 前缀判断是否已密文），对未加密的非空值 encrypt_token 后 raw UPDATE 写回。已密文/空值跳过。

前置：先部署带 EncryptedText 的代码 + 配好 TOKEN_ENCRYPTION_KEY（此时读旧明文兼容、写新
密文），**再**跑本脚本转存量。丢 key = 现存密文不可解需重授权，务必先确认 key 已落 .env。

用法：
  uv run python -m scripts.migrate_encrypt_tokens            # 执行
  uv run python -m scripts.migrate_encrypt_tokens --dry-run  # 只统计明文行数，不改库
"""
from __future__ import annotations

import argparse

from sqlalchemy import text

from core.config import settings
from core.crypto import encrypt_token, is_encrypted
from core.db import engine

_COLS = ["access_token", "refresh_token"]


def migrate(dry_run: bool = False) -> int:
    if not (settings.token_encryption_key or "").strip():
        print("✗ TOKEN_ENCRYPTION_KEY 未配置，无法加密。请先在 .env 设置 Fernet 密钥。")
        return 2

    plain_count = 0
    updates: list[tuple[int, dict]] = []
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, access_token, refresh_token FROM platform_tokens")
        ).fetchall()
        for rid, at, rt in rows:
            new: dict[str, str] = {}
            for col, val in (("access_token", at), ("refresh_token", rt)):
                if val and not is_encrypted(val):
                    new[col] = encrypt_token(val)
                    plain_count += 1
            if new:
                updates.append((rid, new))

    print(("[DRY-RUN] " if dry_run else "") + f"扫描 platform_tokens：{len(rows)} 行，"
          f"待加密字段 {plain_count} 个（{len(updates)} 行）。")
    for rid, new in updates:
        print(f"  - id={rid} 加密 {sorted(new.keys())}")

    if dry_run:
        print("（dry-run，未实际改库）")
        return 0

    if updates:
        with engine.begin() as conn:
            for rid, new in updates:
                set_clause = ", ".join(f"{c}=:{c}" for c in new)
                conn.execute(
                    text(f"UPDATE platform_tokens SET {set_clause} WHERE id=:id"),
                    {**new, "id": rid},
                )
    print("完成。")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="存量 token 明文→密文迁移（幂等）")
    p.add_argument("--dry-run", action="store_true", help="只统计明文行数，不改库")
    raise SystemExit(migrate(dry_run=p.parse_args().dry_run))
