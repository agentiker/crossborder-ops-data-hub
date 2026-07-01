"""业务阈值配置读取层单测（services/biz_config）。

内存 sqlite；验证：查表命中 / 无行回落 settings / int 取整 / 缓存写后失效 /
白名单元数据完整 / 租户隔离。
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import services.biz_config as bc
from core.config import settings
from core.db import Base


@pytest.fixture()
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    # get_config_* 内部若不传 session 会 new core.db.SessionLocal → 指向本测试库
    monkeypatch.setattr("core.db.SessionLocal", lambda: Session())
    bc.clear_config_cache()
    try:
        yield s
    finally:
        s.close()
        bc.clear_config_cache()


def test_default_fallback_when_no_row(session):
    # 无配置行 → 回落 settings
    assert bc.get_config_int("hotsell_daily_units_threshold", account_id="ecom-app", session=session) == \
        settings.hotsell_daily_units_threshold


def test_hit_table_value(session):
    bc.upsert_config_num(session, account_id="ecom-app",
                         config_key="hotsell_daily_units_threshold", value=Decimal("30"))
    session.commit()
    bc.clear_config_cache()
    assert bc.get_config_int("hotsell_daily_units_threshold", account_id="ecom-app", session=session) == 30


def test_int_rounds(session):
    bc.upsert_config_num(session, account_id="ecom-app",
                         config_key="stock_cover_critical_days", value=Decimal("3.6"))
    session.commit()
    bc.clear_config_cache()
    # int 类取整（round）
    assert bc.get_config_int("stock_cover_critical_days", account_id="ecom-app", session=session) == 4


def test_cache_invalidated_on_write(session):
    # 先读一次（默认值进缓存）
    v0 = bc.get_config_int("new_product_lookback_days", account_id="ecom-app", session=session)
    assert v0 == settings.new_product_lookback_days
    # 写入后应清缓存 → 读到新值
    bc.upsert_config_num(session, account_id="ecom-app",
                         config_key="new_product_lookback_days", value=Decimal("30"))
    session.commit()
    assert bc.get_config_int("new_product_lookback_days", account_id="ecom-app", session=session) == 30


def test_tenant_isolation(session):
    bc.upsert_config_num(session, account_id="ecom-app",
                         config_key="hotsell_daily_units_threshold", value=Decimal("30"))
    session.commit()
    bc.clear_config_cache()
    # gtl 租户无配置 → 回落默认，不受 ecom-app 影响
    assert bc.get_config_int("hotsell_daily_units_threshold", account_id="ecom-app-gtl", session=session) == \
        settings.hotsell_daily_units_threshold


def test_delete_falls_back(session):
    bc.upsert_config_num(session, account_id="ecom-app",
                         config_key="fulfillment_warning_hours", value=Decimal("12"))
    session.commit()
    bc.clear_config_cache()
    assert bc.get_config_int("fulfillment_warning_hours", account_id="ecom-app", session=session) == 12
    bc.delete_config(session, account_id="ecom-app", config_key="fulfillment_warning_hours")
    session.commit()
    assert bc.get_config_int("fulfillment_warning_hours", account_id="ecom-app", session=session) == \
        settings.fulfillment_warning_hours


def test_no_account_falls_back(session):
    # account_id 为 None（contextvar 也没设）→ 回落默认
    from core.tenancy import set_current_account
    set_current_account(None)
    assert bc.get_config_int("hotsell_daily_units_threshold", session=session) == \
        settings.hotsell_daily_units_threshold


def test_query_error_falls_back(monkeypatch):
    # 查库抛异常 → fail-safe 回落默认
    bc.clear_config_cache()

    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr("core.db.SessionLocal", boom)
    from core.tenancy import set_current_account
    set_current_account("ecom-app")
    assert bc.get_config_int("hotsell_daily_units_threshold") == \
        settings.hotsell_daily_units_threshold


def test_whitelist_metadata_complete():
    # 每个白名单项元数据齐全（前端渲染 + 校验依赖）
    for key, meta in bc.CONFIGURABLE_KEYS.items():
        assert meta["label"] and meta["type"] in ("int", "float")
        assert meta["source"] in ("biz_config", "return_rate", "replenishment")
        assert meta["group"]
        # default 能从 settings 取到
        assert hasattr(settings, key), f"{key} 不在 settings 中"


def test_default_of_matches_settings():
    assert bc.default_of("hotsell_daily_units_threshold") == Decimal(str(settings.hotsell_daily_units_threshold))
