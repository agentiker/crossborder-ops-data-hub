"""多店发现 + 遍历器测试（多店/多租户 sync 的地基）。

discover_all_shops 扫全表返回每个授权店；run_for_all_shops 逐店切租户跑 flow，
一店失败不阻断其余、末尾 SystemExit 触发 systemd OnFailure 告警。
"""
import pytest
from sqlalchemy.orm import sessionmaker

import flows._shop_discovery as sd
from models.base_models import PlatformToken


def _patch(session, monkeypatch):
    TestSession = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(sd, "SessionLocal", TestSession)


def _add_shop(session, shop_id, account_id, country="ID"):
    session.add(PlatformToken(
        platform="tiktok_shop", country=country, shop_id=shop_id,
        scope_key=f"k-{shop_id}", account_id=account_id,
        access_token="acc", refresh_token="ref",
    ))
    session.commit()


def test_discover_all_shops_returns_every_shop(session, monkeypatch):
    """跨租户全表扫描：两租户各一店都返回。"""
    _patch(session, monkeypatch)
    _add_shop(session, "shopA", "ecom-app")
    _add_shop(session, "shopB", "ecom-app-gtl")
    shops = sd.discover_all_shops()
    assert len(shops) == 2
    assert {s["shop_id"] for s in shops} == {"shopA", "shopB"}
    assert {s["account_id"] for s in shops} == {"ecom-app", "ecom-app-gtl"}


def test_discover_all_shops_empty(session, monkeypatch):
    _patch(session, monkeypatch)
    assert sd.discover_all_shops() == []


def test_discover_single_shop_ok(session, monkeypatch):
    _patch(session, monkeypatch)
    _add_shop(session, "shopA", "ecom-app")
    assert sd.discover_single_shop()["shop_id"] == "shopA"


def test_discover_single_shop_raises_on_multiple(session, monkeypatch):
    """保留单店语义：>1 店仍 raise（向后兼容）。"""
    _patch(session, monkeypatch)
    _add_shop(session, "shopA", "ecom-app")
    _add_shop(session, "shopB", "ecom-app-gtl")
    with pytest.raises(RuntimeError, match="Multiple authorized shops"):
        sd.discover_single_shop()


def test_discover_single_shop_raises_on_zero(session, monkeypatch):
    _patch(session, monkeypatch)
    with pytest.raises(RuntimeError, match="No authorized shop"):
        sd.discover_single_shop()


def test_run_for_all_shops_iterates_each(session, monkeypatch):
    """逐店调用 flow_fn，scope 4 字段透传。"""
    _patch(session, monkeypatch)
    _add_shop(session, "shopA", "ecom-app")
    _add_shop(session, "shopB", "ecom-app-gtl")
    seen = []

    def fake_flow(**scope):
        seen.append((scope["shop_id"], scope["account_id"]))

    sd.run_for_all_shops(fake_flow)
    assert set(seen) == {("shopA", "ecom-app"), ("shopB", "ecom-app-gtl")}


def test_run_for_all_shops_error_isolation(session, monkeypatch):
    """一店抛错：其余店仍同步，末尾 SystemExit（让 OnFailure 告警照常）。"""
    _patch(session, monkeypatch)
    _add_shop(session, "shopA", "ecom-app")
    _add_shop(session, "shopB", "ecom-app-gtl")
    seen = []

    def fake_flow(**scope):
        seen.append(scope["shop_id"])
        if scope["shop_id"] == "shopA":
            raise ValueError("boom")

    with pytest.raises(SystemExit, match="1/2 shop"):
        sd.run_for_all_shops(fake_flow)
    assert set(seen) == {"shopA", "shopB"}  # 两店都被调用，错误未阻断
