"""加密备份脚本测试（plan 审计合规第 5 节）：fail-closed + 滚动保留。

不连真实 DB——只验「口令空拒绝备份」与「滚动按时间戳保留最近 N 份」两条纯逻辑。
"""
from __future__ import annotations

from pathlib import Path

import scripts.backup_db as bk


def test_backup_fail_closed_without_passphrase(monkeypatch):
    """BACKUP_GPG_PASSPHRASE 空 → 返回 2 且不碰 DB（fail closed，绝不写未加密备份）。"""
    monkeypatch.setattr(bk.settings, "backup_gpg_passphrase", "", raising=False)
    assert bk.backup(dry_run=False) == 2


def test_rotate_keeps_recent(monkeypatch, tmp_path):
    """_rotate 按文件名（含时间戳，字典序=时间序）保留最近 keep 份，删更旧的。"""
    monkeypatch.setattr(bk, "BACKUP_DIR", tmp_path)
    names = [f"{bk._PREFIX}2026010{i}-000000{bk._SUFFIX}" for i in range(1, 6)]  # 5 份
    for n in names:
        (tmp_path / n).write_text("x")

    removed = bk._rotate(keep=3)
    assert sorted(removed) == sorted(names[:2])  # 删最旧 2 份
    remaining = sorted(p.name for p in tmp_path.glob(f"{bk._PREFIX}*{bk._SUFFIX}"))
    assert remaining == names[2:]  # 留最近 3 份


def test_rotate_noop_when_under_keep(monkeypatch, tmp_path):
    """份数不足 keep 时不删。"""
    monkeypatch.setattr(bk, "BACKUP_DIR", tmp_path)
    (tmp_path / f"{bk._PREFIX}20260101-000000{bk._SUFFIX}").write_text("x")
    assert bk._rotate(keep=14) == []
