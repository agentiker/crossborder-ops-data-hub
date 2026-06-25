"""每日加密数据库备份（plan 审计合规第 5 节：数据备份）。

mysqldump → gzip → GPG 对称加密（AES256），落 backups/datahub-<ts>.sql.gz.gpg，按个数滚动保留。
合规要「数据可恢复且备份本身加密」：明文 dump 含全量 token（已密文化）+ 业务数据，落盘必须再加一层
GPG，口令 settings.backup_gpg_passphrase（.env BACKUP_GPG_PASSPHRASE）。**口令空 → 直接 raise**
（fail closed，绝不写未加密备份）。

凭据安全：DB 账密写进**临时 defaults-extra-file（chmod 600）**给 mysqldump `--defaults-extra-file`、
GPG 口令写**临时 passphrase-file（chmod 600）**给 `--passphrase-file`——两者都不进 argv（否则
`ps` 可见）。临时文件 finally 删除。

恢复（口令同上，须先有 .env 的 BACKUP_GPG_PASSPHRASE）：
  gpg --batch --passphrase-file <pass> --decrypt datahub-<ts>.sql.gz.gpg | gunzip \
    | mysql --defaults-extra-file=<creds> <database>

用法：
  uv run python -m scripts.backup_db                 # 备份 + 滚动
  uv run python -m scripts.backup_db --keep 30       # 保留最近 30 份（默认 14）
  uv run python -m scripts.backup_db --dry-run       # 只打印将做什么，不实际 dump
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from core.config import settings

# 备份落仓库外的 ~/backups/data-hub（不入库；与代码同盘但独立目录，便于单独搬运/异地同步）。
BACKUP_DIR = Path(os.path.expanduser("~/backups/data-hub"))
_PREFIX = "datahub-"
_SUFFIX = ".sql.gz.gpg"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _write_secret_file(content: str, suffix: str) -> str:
    """写 chmod 600 临时文件，返回路径（凭据/口令不进 argv 用）。"""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


def _rotate(keep: int) -> list[str]:
    """按文件名（含时间戳，字典序=时间序）保留最近 keep 份，删更旧的。返回被删文件名。"""
    files = sorted(
        (p for p in BACKUP_DIR.glob(f"{_PREFIX}*{_SUFFIX}")),
        key=lambda p: p.name,
    )
    removed = []
    for p in files[:-keep] if keep > 0 else []:
        p.unlink()
        removed.append(p.name)
    return removed


def backup(keep: int = 14, dry_run: bool = False) -> int:
    passphrase = (settings.backup_gpg_passphrase or "").strip()
    if not passphrase:
        print("✗ BACKUP_GPG_PASSPHRASE 未配置，拒绝写未加密备份（fail closed）。"
              "请在 .env 设置 GPG 对称口令后重试。", file=sys.stderr)
        return 2

    db = settings.db
    out_path = BACKUP_DIR / f"{_PREFIX}{_ts()}{_SUFFIX}"

    if dry_run:
        print(f"[DRY-RUN] mysqldump {db.database}@{db.host}:{db.port} "
              f"→ gzip → gpg(AES256) → {out_path}")
        print(f"[DRY-RUN] 保留最近 {keep} 份，删更旧的")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    # [client] 段同时供 mysqldump 读取 host/port/user/password。
    creds = (
        "[client]\n"
        f"host={db.host}\nport={db.port}\nuser={db.user}\npassword={db.password}\n"
    )
    creds_file = _write_secret_file(creds, ".cnf")
    pass_file = _write_secret_file(passphrase, ".pass")
    try:
        dump_cmd = [
            "mysqldump", f"--defaults-extra-file={creds_file}",
            "--single-transaction",  # InnoDB 一致快照，不锁表
            "--quick",               # 流式，不把整表读进内存
            "--routines", "--triggers", "--events",
            "--no-tablespaces",      # 免 PROCESS 权限（MySQL 8 默认 dump tablespace 需要）
            db.database,
        ]
        gpg_cmd = [
            "gpg", "--batch", "--yes", "--symmetric", "--cipher-algo", "AES256",
            f"--passphrase-file={pass_file}", "--output", str(out_path),
        ]
        # mysqldump → gzip → gpg 流水线；逐段查 returncode（pipe 的 SIGPIPE 会掩盖 dump 失败）。
        with open(os.devnull, "w"):
            p_dump = subprocess.Popen(dump_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            p_gzip = subprocess.Popen(["gzip", "-c"], stdin=p_dump.stdout, stdout=subprocess.PIPE)
            p_dump.stdout.close()  # 让 gzip 收到 dump 的 SIGPIPE
            p_gpg = subprocess.Popen(gpg_cmd, stdin=p_gzip.stdout, stderr=subprocess.PIPE)
            p_gzip.stdout.close()
            gpg_err = p_gpg.communicate()[1]
            p_gzip.wait()
            dump_err = p_dump.stderr.read()
            p_dump.wait()

        if p_dump.returncode != 0:
            out_path.unlink(missing_ok=True)
            print(f"✗ mysqldump 失败 rc={p_dump.returncode}: "
                  f"{(dump_err or b'').decode(errors='replace')[:400]}", file=sys.stderr)
            return 1
        if p_gpg.returncode != 0 or p_gzip.returncode != 0:
            out_path.unlink(missing_ok=True)
            print(f"✗ gzip/gpg 失败 gzip_rc={p_gzip.returncode} gpg_rc={p_gpg.returncode}: "
                  f"{(gpg_err or b'').decode(errors='replace')[:400]}", file=sys.stderr)
            return 1
    finally:
        for f in (creds_file, pass_file):
            try:
                os.unlink(f)
            except OSError:
                pass

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"✓ 备份完成 {out_path}（{size_mb:.1f} MB）")
    removed = _rotate(keep)
    if removed:
        print(f"  滚动删除 {len(removed)} 份旧备份: {', '.join(removed)}")
    remaining = len(list(BACKUP_DIR.glob(f"{_PREFIX}*{_SUFFIX}")))
    print(f"  当前保留 {remaining} 份（keep={keep}）")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="每日加密数据库备份（mysqldump→gzip→GPG，幂等可重跑）")
    p.add_argument("--keep", type=int, default=14, help="保留最近份数（默认 14）")
    p.add_argument("--dry-run", action="store_true", help="只打印将做什么，不实际备份")
    args = p.parse_args()
    raise SystemExit(backup(keep=args.keep, dry_run=args.dry_run))
