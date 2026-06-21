"""shop_admin CLI（店铺租户归属）+ is_valid_account 测试。"""
from sqlalchemy.orm import sessionmaker

import scripts.shop_admin as sa
from core import tenancy
from models.base_models import PlatformToken


def _patch(session, monkeypatch):
    TestSession = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(sa, "SessionLocal", TestSession)


def _add_shop(session, shop_id, account_id):
    session.add(PlatformToken(
        platform="tiktok_shop", country="ID", shop_id=shop_id,
        scope_key=f"k-{shop_id}", account_id=account_id,
        access_token="a", refresh_token="r",
    ))
    session.commit()


def _allow_gtl(monkeypatch):
    """让 ecom-app-gtl 成为合法租户（host 映射里出现过即合法）。"""
    monkeypatch.setitem(tenancy.settings.tenancy.host_to_account, "test-host", "ecom-app-gtl")


def test_is_valid_account(monkeypatch):
    _allow_gtl(monkeypatch)
    assert tenancy.is_valid_account("ecom-app")        # DEFAULT_ACCOUNT 恒合法
    assert tenancy.is_valid_account("ecom-app-gtl")    # host 映射里
    assert not tenancy.is_valid_account("bogus")


def test_assign_changes_account(session, monkeypatch):
    _patch(session, monkeypatch)
    _allow_gtl(monkeypatch)
    _add_shop(session, "shopX", "ecom-app")
    rc = sa.main(["assign", "--shop-id", "shopX", "--account-id", "ecom-app-gtl"])
    assert rc == 0
    assert session.query(PlatformToken).filter_by(shop_id="shopX").first().account_id == "ecom-app-gtl"


def test_assign_rejects_invalid_account(session, monkeypatch):
    _patch(session, monkeypatch)
    _add_shop(session, "shopX", "ecom-app")
    rc = sa.main(["assign", "--shop-id", "shopX", "--account-id", "bogus"])
    assert rc == 2
    assert session.query(PlatformToken).filter_by(shop_id="shopX").first().account_id == "ecom-app"  # 未改


def test_assign_shop_not_found(session, monkeypatch):
    _patch(session, monkeypatch)
    _allow_gtl(monkeypatch)
    rc = sa.main(["assign", "--shop-id", "nope", "--account-id", "ecom-app-gtl"])
    assert rc == 2
