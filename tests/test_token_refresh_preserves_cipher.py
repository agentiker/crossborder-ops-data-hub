"""刷新 token 不得抹掉 shop_cipher 的回归锁。

TikTok 刷新接口 (/api/v2/token/refresh) 的响应**不含** shop_cipher，历史上
`TikTokShopClient.save_token` 无条件写回 `self.shop_cipher`，导致每次 6 小时刷新
把 DB 里的 cipher 抹成 None → 后续 orders/products search 全报
`400 106013 Missing shop_cipher`（2026-06-13 全站故障根因）。

此处锁定：save_token 在 `self.shop_cipher` 为空时**保留** DB 旧值，不覆盖。

同源回归（2026-06-21）：`refresh_token` 也踩了一模一样的坑——刷新响应可能返回空/None
refresh_token，无条件写回会把 DB 抹成 NULL → 刷新任务因 `refresh_token IS NOT NULL`
永久排除该行（静默"找到 0 个"）、access_token 到期后同步无法自救只能人工重新授权。
本文件一并锁定 refresh_token 的空值保护。
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


def test_refresh_without_refresh_token_preserves_existing(session, monkeypatch):
    """刷新响应空 refresh_token 时，保留 DB 旧 refresh_token 及其有效期，绝不抹成 NULL。

    根因回归锁（2026-06-21）：refresh_token 被抹 NULL → refresh flow 因
    `refresh_token IS NOT NULL` 永久排除该行、access_token 到期后同步无法自救。
    """
    _patch_sessionlocal(session, monkeypatch)
    client = TikTokShopClient(country="ID", shop_id="shop-1", auto_load_token=False)

    # 初次授权：refresh_token + 有效期入库
    client.access_token = "acc-1"
    client.refresh_token = "ref-1"
    client.token_expire_at = datetime(2026, 1, 8, tzinfo=timezone.utc).timestamp()
    client.refresh_token_expire_at = datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp()
    client.save_token(token_payload={"v": 1})

    row = session.query(PlatformToken).filter_by(scope_key=_scope_key()).one()
    assert row.refresh_token == "ref-1"
    old_refresh_exp = row.refresh_token_expire_at

    # 模拟刷新：新 access_token，但响应未返回 refresh_token（None）+ 其有效期置 0
    client.access_token = "acc-2"
    client.refresh_token = None
    client.token_expire_at = datetime(2026, 1, 15, tzinfo=timezone.utc).timestamp()
    client.refresh_token_expire_at = 0
    client.save_token(token_payload={"v": 2})

    session.expire_all()
    row = session.query(PlatformToken).filter_by(scope_key=_scope_key()).one()
    assert row.access_token == "acc-2"                      # access 正常更新
    assert row.refresh_token == "ref-1"                     # refresh_token 保留、未抹 NULL
    assert row.refresh_token_expire_at == old_refresh_exp   # 有效期一并保留


def test_save_token_rotates_refresh_token_when_present(session, monkeypatch):
    """刷新返回了新 refresh_token 时正常滚动更新（兜底逻辑不误伤正常 rotation）。"""
    _patch_sessionlocal(session, monkeypatch)
    client = TikTokShopClient(country="ID", shop_id="shop-1", auto_load_token=False)
    client.access_token = "acc-1"
    client.refresh_token = "ref-1"
    client.token_expire_at = datetime(2026, 1, 8, tzinfo=timezone.utc).timestamp()
    client.refresh_token_expire_at = datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp()
    client.save_token(token_payload={"v": 1})

    # 刷新返回新 refresh_token（TikTok 正常 rotation）
    client.access_token = "acc-2"
    client.refresh_token = "ref-2"
    client.refresh_token_expire_at = datetime(2026, 3, 20, tzinfo=timezone.utc).timestamp()
    client.save_token(token_payload={"v": 2})

    session.expire_all()
    row = session.query(PlatformToken).filter_by(scope_key=_scope_key()).one()
    assert row.refresh_token == "ref-2"  # 正常滚动更新
