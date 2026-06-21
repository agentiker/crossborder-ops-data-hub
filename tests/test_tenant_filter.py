"""ORM 自动租户过滤测试（do_orm_execute + with_loader_criteria）。

验证：
- contextvar 显式设定 → WHERE account_id = ? 自动注入
- contextvar 未设定 → 不过滤（向后兼容）
- TENANT_BYPASS → 不过滤
- WebConversation / WebMessage（无 account_id 列）不受影响
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from core.db import Base, _inject_tenant_filter
from models.base_models import Inventory, OrderHeader, WebConversation


@pytest.fixture()
def tenant_session():
    """带自动租户过滤的内存 session。"""
    from core.tenancy import _current_account

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    event.listen(Session, "do_orm_execute", _inject_tenant_filter)
    db_session = Session()
    try:
        yield db_session
    finally:
        db_session.close()
        Base.metadata.drop_all(bind=engine)
        _current_account.set(None)


def _inv(platform, country, shop_id, seller_id, account_id, sku_id,
         product_id, warehouse_id, idempotency_key):
    """构建 Inventory 测试行的 helper。"""
    return Inventory(
        platform=platform, country=country, shop_id=shop_id,
        seller_id=seller_id, account_id=account_id, sku_id=sku_id,
        product_id=product_id, warehouse_id=warehouse_id,
        idempotency_key=idempotency_key,
    )


def _seed(session, model, rows: list[dict]):
    """批量插入测试数据。"""
    for r in rows:
        session.add(model(**r))
    session.commit()


# ── 核心过滤测试 ────────────────────────────────────────────────────────────────


def test_filter_active_when_account_set(tenant_session):
    """contextvar 显式设定 → 只返回该租户数据。"""
    from core.tenancy import set_current_account

    tenant_session.add_all([
        _inv("tiktok_shop", "ID", "s1", "u1", "ecom-app", "SKU-A", "P1", "WH1", "k1"),
        _inv("tiktok_shop", "ID", "s2", "u2", "gtl", "SKU-B", "P2", "WH1", "k2"),
    ])
    tenant_session.commit()

    set_current_account("ecom-app")
    rows = tenant_session.query(Inventory).all()
    assert len(rows) == 1
    assert rows[0].sku_id == "SKU-A"


def test_no_filter_when_account_not_set(tenant_session):
    """contextvar 未设定 → 返回全量数据（向后兼容）。"""
    from core.tenancy import _current_account

    _current_account.set(None)
    tenant_session.add_all([
        _inv("tiktok_shop", "ID", "s1", "u1", "ecom-app", "SKU-A", "P1", "WH1", "k1"),
        _inv("tiktok_shop", "ID", "s2", "u2", "gtl", "SKU-B", "P2", "WH1", "k2"),
    ])
    tenant_session.commit()

    rows = tenant_session.query(Inventory).all()
    assert len(rows) == 2


def test_no_filter_when_bypass(tenant_session):
    """TENANT_BYPASS → 返回全量数据。"""
    from core.tenancy import TENANT_BYPASS, set_current_account

    tenant_session.add_all([
        _inv("tiktok_shop", "ID", "s1", "u1", "ecom-app", "SKU-A", "P1", "WH1", "k1"),
        _inv("tiktok_shop", "ID", "s2", "u2", "gtl", "SKU-B", "P2", "WH1", "k2"),
    ])
    tenant_session.commit()

    set_current_account(TENANT_BYPASS)
    rows = tenant_session.query(Inventory).all()
    assert len(rows) == 2


def test_unaffected_model_without_account_id(tenant_session):
    """WebConversation（无 account_id 列）不受自动过滤影响。"""
    from core.tenancy import set_current_account

    tenant_session.add_all([
        WebConversation(open_id="ou_1", title="conv-1"),
        WebConversation(open_id="ou_2", title="conv-2"),
    ])
    tenant_session.commit()

    set_current_account("ecom-app")
    rows = tenant_session.query(WebConversation).all()
    assert len(rows) == 2


def test_filter_on_order_header(tenant_session):
    """OrderHeader 也能被自动过滤。"""
    from core.tenancy import set_current_account

    tenant_session.add_all([
        OrderHeader(platform="tiktok_shop", country="ID", shop_id="s1",
                    order_id="ORD-1", account_id="ecom-app",
                    idempotency_key="ek1"),
        OrderHeader(platform="tiktok_shop", country="ID", shop_id="s2",
                    order_id="ORD-2", account_id="gtl",
                    idempotency_key="ek2"),
    ])
    tenant_session.commit()

    set_current_account("gtl")
    rows = tenant_session.query(OrderHeader).all()
    assert len(rows) == 1
    assert rows[0].order_id == "ORD-2"


def test_switching_tenants_same_session_no_fixation(tenant_session):
    """同一 session 连续切租户必须各取各的（回归：防 lambda 缓存固化跨租户泄漏）。

    生产 SessionLocal 是模块级单例、被多请求复用。早期 track_closure_variables=False
    会把首个请求的 account_id 烤进进程级 lambda 缓存，导致第二个租户读到第一个租户的数据。
    每个用例只查一次的测试抓不到，必须在同一 session 上连续切租户才能暴露。
    """
    from core.tenancy import set_current_account

    tenant_session.add_all([
        OrderHeader(platform="tiktok_shop", country="ID", shop_id="s1",
                    order_id="ORD-ecom", account_id="ecom-app",
                    idempotency_key="ek1"),
        OrderHeader(platform="tiktok_shop", country="ID", shop_id="s2",
                    order_id="ORD-gtl", account_id="gtl",
                    idempotency_key="ek2"),
    ])
    tenant_session.commit()

    # 来回切多次，每次都必须只看到自己租户的行
    for account_id, expected in [
        ("ecom-app", "ORD-ecom"),
        ("gtl", "ORD-gtl"),
        ("ecom-app", "ORD-ecom"),
        ("gtl", "ORD-gtl"),
    ]:
        set_current_account(account_id)
        rows = tenant_session.query(OrderHeader).all()
        assert [r.order_id for r in rows] == [expected], (
            f"set {account_id} 却看到 {[r.order_id for r in rows]}（疑似 lambda 缓存固化跨租户泄漏）"
        )
