"""刷新 token 不得抹掉 shop_cipher 的回归锁。

TikTok 刷新接口 (/api/v2/token/refresh) 的响应**不含** shop_cipher，历史上
`TikTokShopClient.save_token` 无条件写回 `self.shop_cipher`，导致每次 6 小时刷新
把 DB 里的 cipher 抹成 None → 后续 orders/products search 全报
`400 106013 Missing shop_cipher`（2026-06-13 全站故障根因）。

此处锁定：save_token 在 `self.shop_cipher` 为空时**保留** DB 旧值，不覆盖。
"""
from datetime import datetime, timezone

from sqlalchemy.orm import sessionmaker

from models.base_models import PlatformToken
from platforms.tiktok_shop.client import TikTokShopClient
from services.scoping import build_scope_key


def _patch_sessionlocal(session, monkeypatch):
    """client.save_token 内部用 core.db.SessionLocal；指向同一 in-memory engine 的
    独立 session 工厂（每次新 session，save_token 的 finally close() 不影响测试 session）。"""
    import core.db as core_db

    TestSession = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(core_db, "SessionLocal", TestSession)


def _scope_key():
    return build_scope_key(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        seller_id=None, account_id=None,
    )


def test_refresh_without_cipher_preserves_existing(session, monkeypatch):
    _patch_sessionlocal(session, monkeypatch)
    client = TikTokShopClient(country="ID", shop_id="shop-1", auto_load_token=False)

    # 初次授权：拿到 cipher 并入库
    client.access_token = "acc-1"
    client.refresh_token = "ref-1"
    client.shop_cipher = "ROW_CIPHER_X"
    client.token_expire_at = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    client.save_token(token_payload={"v": 1})

    row = session.query(PlatformToken).filter_by(scope_key=_scope_key()).one()
    assert row.shop_cipher == "ROW_CIPHER_X"

    # 模拟刷新：新 access_token，shop_cipher 为空（刷新响应不含 cipher）
    client.access_token = "acc-2"
    client.shop_cipher = None
    client.save_token(token_payload={"v": 2})

    session.expire_all()
    row = session.query(PlatformToken).filter_by(scope_key=_scope_key()).one()
    assert row.access_token == "acc-2"        # 正常更新
    assert row.shop_cipher == "ROW_CIPHER_X"  # cipher 保留、未被刷新抹掉


def test_save_token_writes_cipher_when_present(session, monkeypatch):
    """正常授权路径：带 cipher 时正常写入（确保兜底逻辑没误伤正常写入）。"""
    _patch_sessionlocal(session, monkeypatch)
    client = TikTokShopClient(country="ID", shop_id="shop-1", auto_load_token=False)
    client.access_token = "acc-1"
    client.refresh_token = "ref-1"
    client.shop_cipher = "ROW_NEW"
    client.save_token(token_payload={"v": 1})

    row = session.query(PlatformToken).filter_by(scope_key=_scope_key()).one()
    assert row.shop_cipher == "ROW_NEW"
