from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("TIKTOK__APP_KEY", "test_app_key")
os.environ.setdefault("TIKTOK__APP_SECRET", "test_app_secret")
# token 列改 EncryptedText 后未配 key 写 token 会 fail-closed raise；测试给个一次性 Fernet key
# （须在 core.config.settings 实例化前设入 env）。
from cryptography.fernet import Fernet as _Fernet  # noqa: E402

os.environ.setdefault("TOKEN_ENCRYPTION_KEY", _Fernet.generate_key().decode())

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db import Base
from models import base_models  # noqa: F401


@pytest.fixture(autouse=True)
def _isolate_audit_db(monkeypatch):
    """把审计独立 session（services.audit.SessionLocal）重定向到一次性 sqlite。

    log_*_safe 走独立 session，默认连 DATABASE_URL（hp 的 MySQL）——deploy-hp 会在 hp 上
    跑 pytest，若不隔离，client/OAuth/flow/chat 等独立 session 审计会把测试垃圾直写进真实
    hp 的不可篡改哈希链。StaticPool + 单内存库让同进程内多次 SessionLocal() 共享同一 sqlite。"""
    import services.audit as audit_mod

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )
    Base.metadata.create_all(bind=eng)
    monkeypatch.setattr(
        audit_mod, "SessionLocal", sessionmaker(bind=eng, expire_on_commit=False)
    )
    yield


@pytest.fixture()
def session():
    from core.tenancy import _current_account, set_current_account

    set_current_account("ecom-app")
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db_session = Session()
    try:
        yield db_session
    finally:
        db_session.close()
        Base.metadata.drop_all(bind=engine)
        _current_account.set(None)  # 重置，防跨测试污染
