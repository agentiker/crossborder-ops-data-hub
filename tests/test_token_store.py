from datetime import datetime, timezone

from services.token_store import TokenScope, build_token_key, load_token, save_token


def test_build_token_key_includes_multi_shop_scope():
    key = build_token_key(
        TokenScope(
            platform="TikTok_Shop",
            country="VN",
            seller_id="Seller-1",
            shop_id="Shop-9",
            account_id="Ads-3",
        )
    )

    assert key == (
        "platform=TikTok_Shop|country=VN|shop=Shop-9|"
        "seller=Seller-1|account=Ads-3|warehouse=_"
    )


def test_save_and_load_token_are_scoped_per_shop(session):
    expire_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    shop_a = TokenScope("tiktok_shop", country="VN", seller_id="seller", shop_id="a")
    shop_b = TokenScope("tiktok_shop", country="VN", seller_id="seller", shop_id="b")

    save_token(
        session,
        scope=shop_a,
        access_token="access-a",
        refresh_token="refresh-a",
        token_expire_at=expire_at,
    )
    save_token(
        session,
        scope=shop_b,
        access_token="access-b",
        refresh_token="refresh-b",
        token_expire_at=expire_at,
    )
    session.commit()

    assert load_token(session, scope=shop_a).access_token == "access-a"
    assert load_token(session, scope=shop_b).access_token == "access-b"


def test_save_token_updates_existing_scope_without_duplicate(session):
    scope = TokenScope("tiktok_shop", country="VN", seller_id="seller", shop_id="a")
    expire_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    save_token(
        session,
        scope=scope,
        access_token="old",
        refresh_token="old-refresh",
        token_expire_at=expire_at,
    )
    save_token(
        session,
        scope=scope,
        access_token="new",
        refresh_token="new-refresh",
        token_expire_at=expire_at,
    )
    session.commit()

    loaded = load_token(session, scope=scope)
    assert loaded.access_token == "new"
    assert loaded.refresh_token == "new-refresh"
