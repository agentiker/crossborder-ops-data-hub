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
    """核心隔离：_scan_one 按收件人 account 解析范围——gtl 收件人不扫 ecom 的店。"""
    from services import scope_resolution
    from models.base_models import BusinessScope, PlatformToken

    _use(session, monkeypatch)
    monkeypatch.setattr(scope_resolution, "SessionLocal", lambda: session)
    # ecom 拥有 s_ecom；gtl 经 scope 授权 s_gtl（各自独立）
    session.add(PlatformToken(platform="tiktok_shop", country="ID", shop_id="s_ecom",
                              account_id="ecom-app", scope_key="k1"))
    session.add(BusinessScope(account_id="ecom-app-gtl", scope_key="g", scope_name="g",
                              scope_type="shop_group", platform="tiktok_shop", country="ID",
                              shop_ids=["s_gtl"], is_active=True))
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
