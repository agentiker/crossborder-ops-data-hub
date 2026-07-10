"""Phase 6：告警收件人迁 DB（alert_recipients）+ load_recipients 行为锁定。"""
from __future__ import annotations

from flows import scan_fulfillment_alerts as scan
from models.base_models import AlertRecipient


def _use(session, monkeypatch):
    monkeypatch.setattr(scan, "SessionLocal", lambda: session)


def _add(session, account, open_id, scope_key=None, active=True):
    session.add(AlertRecipient(
        channel="feishu", account_id=account, open_id=open_id,
        scope_key=scope_key, is_active=active,
    ))


def test_load_recipients_empty_falls_back_to_constant(session, monkeypatch):
    """表空 → 回落内置常量（迁移过渡期告警不静默中断）。"""
    _use(session, monkeypatch)
    out = scan.load_recipients()
    assert out == scan._FALLBACK_RECIPIENTS


def test_load_recipients_reads_db_when_present(session, monkeypatch):
    """表有数据 → 只读 DB（不再用常量），按 account/scope 映射。"""
    _use(session, monkeypatch)
    _add(session, "ecom-app", "ou_boss", scope_key=None)
    _add(session, "ecom-app-gtl", "ou_gtl", scope_key="tts-id-all")
    _add(session, "ecom-app", "ou_off", scope_key=None, active=False)  # 停用不返回
    session.commit()

    out = scan.load_recipients()
    assert {(r["account"], r["scope_id"]) for r in out} == {
        ("ecom-app", None),
        ("ecom-app-gtl", "tts-id-all"),
    }
    assert all(r["open_id"] != "ou_off" for r in out)  # 停用的不在内


def test_scan_one_resolves_scope_per_recipient_account(session, monkeypatch):
    """核心隔离：_scan_one 按收件人 account 解析范围——gtl 收件人不扫 ecom 的店。

    2026-07 起范围从 user_roles 派生（权限∩订阅），故收件人须有 user_roles 行（此处 boss）。
    """
    from services import scope_resolution, user_authz
    from models.base_models import BusinessScope, PlatformToken, UserRole

    _use(session, monkeypatch)
    monkeypatch.setattr(scope_resolution, "SessionLocal", lambda: session)
    monkeypatch.setattr(user_authz, "SessionLocal", lambda: session)
    # ecom 拥有 s_ecom；gtl 经 scope 授权 s_gtl（各自独立）
    session.add(PlatformToken(platform="tiktok_shop", country="ID", shop_id="s_ecom",
                              account_id="ecom-app", scope_key="k1"))
    session.add(BusinessScope(account_id="ecom-app-gtl", scope_key="g", scope_name="g",
                              scope_type="shop_group", platform="tiktok_shop", country="ID",
                              shop_ids=["s_gtl"], is_active=True))
    # 权限真相源：两个收件人都是各自租户的 boss
    session.add(UserRole(channel="feishu", account_id="ecom-app", open_id="ou1",
                         role="boss", is_active=True))
    session.add(UserRole(channel="feishu", account_id="ecom-app-gtl", open_id="ou2",
                         role="boss", is_active=True))
    session.commit()

    captured = {}

    def fake_rule(sess, *, account, open_id, scope, scope_id, dry_run):
        captured[account] = list(scope.shop_ids)
        return "ok"

    monkeypatch.setattr(scan, "_scan_fulfillment", fake_rule)
    monkeypatch.setattr(scan, "_scan_stock", fake_rule)

    scan._scan_one({"account": "ecom-app", "open_id": "ou1", "scope_id": None}, dry_run=True)
    scan._scan_one({"account": "ecom-app-gtl", "open_id": "ou2", "scope_id": None}, dry_run=True)

    assert captured["ecom-app"] == ["s_ecom"]          # ecom 只扫自己的店
    assert captured["ecom-app-gtl"] == ["s_gtl"]        # gtl 只扫自己的店，绝不含 s_ecom


# ── 范围 = 权限(user_roles) ∩ 订阅(alert_recipients.scope_key) ──────────────


def _seed_two_shop_tenant(session):
    """租户 ecom-app 两个店 s1/s2 + 单店 scope only-s1/only-s2。"""
    from models.base_models import BusinessScope, PlatformToken

    for sid in ("s1", "s2"):
        session.add(PlatformToken(platform="tiktok_shop", country="ID", shop_id=sid,
                                  account_id="ecom-app", scope_key=f"tk-{sid}"))
        session.add(BusinessScope(account_id="ecom-app", scope_key=f"only-{sid}",
                                  scope_name=sid, scope_type="single_shop",
                                  platform="tiktok_shop", country="ID",
                                  shop_ids=[sid], is_active=True))


def _patch_all(session, monkeypatch):
    from services import scope_resolution, user_authz

    _use(session, monkeypatch)
    monkeypatch.setattr(scope_resolution, "SessionLocal", lambda: session)
    monkeypatch.setattr(user_authz, "SessionLocal", lambda: session)


def _patch_rules(monkeypatch, captured):
    def fake_rule(sess, *, account, open_id, scope, scope_id, dry_run):
        captured["shops"] = sorted(scope.shop_ids)
        return "ok"

    for r in ("_scan_fulfillment", "_scan_stock", "_scan_fee_rate",
              "_scan_unsettled_fee_rate", "_scan_hotsell"):
        monkeypatch.setattr(scan, r, fake_rule)


def test_recipient_without_user_role_is_skipped_fail_closed(session, monkeypatch):
    """无 user_roles 记录（或停用）→ fail-closed 跳过，绝不回落全量。"""
    _patch_all(session, monkeypatch)
    _seed_two_shop_tenant(session)
    session.commit()

    out = scan._scan_one({"account": "ecom-app", "open_id": "ou_ghost", "scope_id": None},
                         dry_run=True)
    assert len(out) == 1 and "跳过" in out[0] and "user_roles" in out[0]


def test_subscription_narrows_within_permission(session, monkeypatch):
    """boss(全店权限) + 订阅 only-s1 → 只扫 s1（订阅在权限内收窄生效）。"""
    from models.base_models import UserRole

    _patch_all(session, monkeypatch)
    _seed_two_shop_tenant(session)
    session.add(UserRole(channel="feishu", account_id="ecom-app", open_id="ou_b",
                         role="boss", is_active=True))
    session.commit()

    captured = {}
    _patch_rules(monkeypatch, captured)
    scan._scan_one({"account": "ecom-app", "open_id": "ou_b", "scope_id": "only-s1"},
                   dry_run=True)
    assert captured["shops"] == ["s1"]


def test_boss_null_subscription_gets_all_shops(session, monkeypatch):
    """boss + 订阅 NULL → 租户全部店（默认行为不变）。"""
    from models.base_models import UserRole

    _patch_all(session, monkeypatch)
    _seed_two_shop_tenant(session)
    session.add(UserRole(channel="feishu", account_id="ecom-app", open_id="ou_b2",
                         role="boss", is_active=True))
    session.commit()

    captured = {}
    _patch_rules(monkeypatch, captured)
    scan._scan_one({"account": "ecom-app", "open_id": "ou_b2", "scope_id": None},
                   dry_run=True)
    assert captured["shops"] == ["s1", "s2"]


def test_operator_clamped_to_allowed_scope(session, monkeypatch):
    """operator(allowed=only-s1) + 订阅 NULL → 夹到 s1，不是租户全店。"""
    from models.base_models import UserRole

    _patch_all(session, monkeypatch)
    _seed_two_shop_tenant(session)
    session.add(UserRole(channel="feishu", account_id="ecom-app", open_id="ou_op",
                         role="operator", allowed_scope_key="only-s1", is_active=True))
    session.commit()

    captured = {}
    _patch_rules(monkeypatch, captured)
    scan._scan_one({"account": "ecom-app", "open_id": "ou_op", "scope_id": None},
                   dry_run=True)
    assert captured["shops"] == ["s1"]  # 权限上限生效，绝不放大到 s2


def test_subscription_beyond_permission_is_clamped(session, monkeypatch):
    """operator(allowed=only-s1) + 订阅 only-s2（越权）→ fail-closed 跳过，绝不发 s2 数据。"""
    from models.base_models import UserRole

    _patch_all(session, monkeypatch)
    _seed_two_shop_tenant(session)
    session.add(UserRole(channel="feishu", account_id="ecom-app", open_id="ou_op2",
                         role="operator", allowed_scope_key="only-s1", is_active=True))
    session.commit()

    out = scan._scan_one({"account": "ecom-app", "open_id": "ou_op2", "scope_id": "only-s2"},
                         dry_run=True)
    assert len(out) == 1 and "跳过" in out[0]
