"""补货配置读写单测：系数默认回退/部分覆盖/范围回退、超级爆品标记与撤销。"""
from __future__ import annotations

from core.config import settings
from services.replenishment_config import (
    get_effective_config,
    get_super_hot_product_ids,
    set_super_hot,
    upsert_config,
)


def test_config_defaults_when_no_row(session):
    cfg = get_effective_config(session, account_id="a", scope_key="sc1")
    assert cfg.velocity_days == settings.replenish_velocity_days
    assert cfg.normal_multiplier == settings.replenish_normal_multiplier
    assert cfg.superhot_multiplier == settings.replenish_superhot_multiplier


def test_config_row_overrides(session):
    upsert_config(session, account_id="a", scope_key="sc1",
                  velocity_days=45, normal_multiplier=1.8, superhot_multiplier=3.0)
    session.commit()
    cfg = get_effective_config(session, account_id="a", scope_key="sc1")
    assert (cfg.velocity_days, cfg.normal_multiplier, cfg.superhot_multiplier) == (45, 1.8, 3.0)


def test_config_partial_override_falls_back_per_field(session):
    """只覆盖 normal_multiplier，其余字段仍用默认。"""
    upsert_config(session, account_id="a", scope_key="sc1", normal_multiplier=2.5)
    session.commit()
    cfg = get_effective_config(session, account_id="a", scope_key="sc1")
    assert cfg.normal_multiplier == 2.5
    assert cfg.velocity_days == settings.replenish_velocity_days  # 未覆盖→默认
    assert cfg.superhot_multiplier == settings.replenish_superhot_multiplier


def test_config_scope_falls_back_to_tenant_level(session):
    """范围无行 → 回退租户级(scope_key=None)行。"""
    upsert_config(session, account_id="a", scope_key=None, normal_multiplier=1.9)
    session.commit()
    cfg = get_effective_config(session, account_id="a", scope_key="any-scope")
    assert cfg.normal_multiplier == 1.9


def test_super_hot_mark_and_revoke(session):
    set_super_hot(session, product_id="p1", account_id="a", note="冬季爆款")
    set_super_hot(session, product_id="p2", account_id="a")
    session.commit()
    assert get_super_hot_product_ids(session, account_id="a") == {"p1", "p2"}
    # 撤销 p2
    set_super_hot(session, product_id="p2", account_id="a", is_active=False)
    session.commit()
    assert get_super_hot_product_ids(session, account_id="a") == {"p1"}


def test_super_hot_scoped_by_account(session):
    set_super_hot(session, product_id="p1", account_id="a")
    set_super_hot(session, product_id="p9", account_id="b")
    session.commit()
    assert get_super_hot_product_ids(session, account_id="a") == {"p1"}
    assert get_super_hot_product_ids(session, account_id="b") == {"p9"}
