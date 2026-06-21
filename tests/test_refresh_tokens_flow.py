"""refresh flow 的"无法自救"告警回归锁。

已/即将过期但 refresh_token 缺失（NULL/空）的 token 被主查询（refresh_token IS NOT NULL）
排除，无法自动刷新。历史上 flow 对它们静默"找到 0 个"，直到 access_token 到期、数据同步
报错才暴露（2026-06-21 烧了数天无告警）。本文件锁定：flow 末尾捞出这些 token 并抛错，
让 systemd OnFailure → 飞书告警，提示人工重新授权。

用 `.fn` 直接调底层函数绕过 Prefect runtime（stuck token 被主查询排除、不进刷新 task，
故不触网）。过期时间用 2020（naive）避开 sqlite aware/naive datetime 字符串比较的坑。
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import sessionmaker

import flows.refresh_tokens as rt
from models.base_models import PlatformToken


def _patch(session, monkeypatch):
    TestSession = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(rt, "SessionLocal", TestSession)
    monkeypatch.setattr(rt, "init_db", lambda: None)
    monkeypatch.setattr(rt, "log_egress_ip", lambda: None)


def _add_token(session, scope_key, *, refresh_token, expire):
    session.add(
        PlatformToken(
            platform="tiktok_shop",
            country="ID",
            shop_id=scope_key,
            scope_key=scope_key,
            access_token="acc",
            refresh_token=refresh_token,
            token_expire_at=expire,
        )
    )
    session.commit()


def test_flow_raises_on_expired_token_without_refresh(session, monkeypatch):
    """过期且无 refresh_token（NULL）→ flow 抛错触发 OnFailure 告警。"""
    _patch(session, monkeypatch)
    _add_token(session, "scope-stuck", refresh_token=None, expire=datetime(2020, 1, 1))

    with pytest.raises(RuntimeError, match="需人工重新授权"):
        rt.refresh_tokens_flow.fn()


def test_flow_raises_on_expired_token_with_empty_refresh(session, monkeypatch):
    """过期且 refresh_token 为空串 "" → 同样视为无法自救，告警。"""
    _patch(session, monkeypatch)
    _add_token(session, "scope-empty", refresh_token="", expire=datetime(2020, 1, 1))

    with pytest.raises(RuntimeError, match="需人工重新授权"):
        rt.refresh_tokens_flow.fn()


def test_flow_clean_when_token_has_refresh_and_not_expiring(session, monkeypatch):
    """未过期、有 refresh_token → 不在刷新窗口、无 stuck，flow 正常返回不抛错。"""
    _patch(session, monkeypatch)
    _add_token(session, "scope-ok", refresh_token="ref-1", expire=datetime(2099, 1, 1))

    result = rt.refresh_tokens_flow.fn()
    assert result["needs_reauth"] == []
