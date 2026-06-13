"""authenticate() 必须用 get-authorized-shops 补 shop_cipher 并按店落库的回归锁。

根因（2026-06-13）：/api/v2/token/get 不返回 shop_cipher/shop_id（实测仅 token、
granted_scopes、open_id、seller_name、seller_base_region、user_type）。旧 authenticate
直接 save_token，client 默认 country=GLOBAL/shop=None → 生成 country=GLOBAL|shop=_ 无
cipher 的占位脏行，未命中真实店行，finance/order 等需 cipher 的接口全失败。

此处锁定：authenticate 换 token 后调 get_authorized_shops，按 region/shop_id/cipher 落每个店行。
"""
from sqlalchemy.orm import sessionmaker

from models.base_models import PlatformToken
from platforms.tiktok_shop.client import TikTokShopClient


def _patch_sessionlocal(session, monkeypatch):
    import core.db as core_db

    TestSession = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(core_db, "SessionLocal", TestSession)


# /api/v2/token/get 的真实返回形状（无 shop_cipher / shop_id）
_TOKEN_PAYLOAD = {
    "data": {
        "access_token": "acc-new",
        "refresh_token": "ref-new",
        "access_token_expire_in": 9999999999,
        "refresh_token_expire_in": 9999999999,
        "granted_scopes": ["seller.order.info", "seller.finance.info"],
        "open_id": "ou_x",
        "seller_name": "SANDBOX",
        "seller_base_region": "ID",
        "user_type": 0,
    }
}


def test_authenticate_fetches_cipher_and_saves_per_shop(session, monkeypatch):
    _patch_sessionlocal(session, monkeypatch)
    client = TikTokShopClient(auto_load_token=False)  # 默认 country=GLOBAL/shop=None

    monkeypatch.setattr(client, "_auth_get", lambda *a, **k: _TOKEN_PAYLOAD)
    monkeypatch.setattr(
        client,
        "get_authorized_shops",
        lambda: [{"id": "7494", "region": "ID", "name": "SANDBOX_ID", "cipher": "ROW_CIPHER_Y"}],
    )

    client.authenticate("auth-code-xyz")

    rows = session.query(PlatformToken).all()
    # 只应有真实店行，不得有 country=GLOBAL|shop=_ 占位脏行
    assert len(rows) == 1
    row = rows[0]
    assert row.country == "ID"
    assert row.shop_id == "7494"
    assert row.shop_cipher == "ROW_CIPHER_Y"   # cipher 来自 get-shops，已补全
    assert row.access_token == "acc-new"
    assert "GLOBAL" not in row.scope_key


def test_authenticate_multi_shop_saves_each(session, monkeypatch):
    _patch_sessionlocal(session, monkeypatch)
    client = TikTokShopClient(auto_load_token=False)
    monkeypatch.setattr(client, "_auth_get", lambda *a, **k: _TOKEN_PAYLOAD)
    monkeypatch.setattr(
        client,
        "get_authorized_shops",
        lambda: [
            {"id": "1001", "region": "ID", "cipher": "CIPH_A"},
            {"id": "2002", "region": "MY", "cipher": "CIPH_B"},
        ],
    )

    client.authenticate("code")

    rows = {r.shop_id: r for r in session.query(PlatformToken).all()}
    assert set(rows) == {"1001", "2002"}
    assert rows["1001"].country == "ID" and rows["1001"].shop_cipher == "CIPH_A"
    assert rows["2002"].country == "MY" and rows["2002"].shop_cipher == "CIPH_B"


def test_authenticate_fallback_when_no_shops(session, monkeypatch):
    """get-authorized-shops 返回空时：按 seller_base_region 兜底存（无 cipher），不崩。"""
    _patch_sessionlocal(session, monkeypatch)
    client = TikTokShopClient(auto_load_token=False)
    monkeypatch.setattr(client, "_auth_get", lambda *a, **k: _TOKEN_PAYLOAD)
    monkeypatch.setattr(client, "get_authorized_shops", lambda: [])

    client.authenticate("code")

    rows = session.query(PlatformToken).all()
    assert len(rows) == 1
    # 兜底用 seller_base_region=ID（而非默认 GLOBAL）
    assert rows[0].country == "ID"
    assert rows[0].shop_cipher is None
