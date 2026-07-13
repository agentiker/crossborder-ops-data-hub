"""审计底座测试（plan 审计合规第 2 节）：哈希链连续性 + 断链检测 + 分链隔离 + 截断。

用 in-memory sqlite（conftest.session fixture）。哈希链按 account_id 分链；FOR UPDATE 在
sqlite 自动省略（见 services.audit._append_with_chain 的方言判断）。
"""
from __future__ import annotations

from models.base_models import ApiCallLog, AuditLog
from services.audit import (
    api_call_canonical_parts,
    audit_event_canonical_parts,
    chain_tips,
    compute_row_hash,
    record_api_call,
    record_audit_event,
    reseal_chain,
    verify_chain,
)


def _verify_chain(session, model, canonical_fn, account_id: str) -> bool:
    rows = (
        session.query(model)
        .filter(model.account_id == account_id)
        .order_by(model.id)
        .all()
    )
    prev = None
    for r in rows:
        if r.prev_hash != prev:
            return False
        if r.row_hash != compute_row_hash(prev, canonical_fn(r)):
            return False
        prev = r.row_hash
    return True


def test_audit_event_chain_continuous(session):
    for i in range(5):
        record_audit_event(
            session, event_type="account_op", event_action="test.op",
            actor_open_id="ou_x", actor_source="cli", target=f"t{i}",
            summary=f"op {i}", account_id="ecom-app",
        )
    session.flush()
    rows = (
        session.query(AuditLog)
        .filter(AuditLog.account_id == "ecom-app")
        .order_by(AuditLog.id)
        .all()
    )
    assert len(rows) == 5
    assert rows[0].prev_hash is None
    assert all(rows[i].prev_hash == rows[i - 1].row_hash for i in range(1, 5))
    assert _verify_chain(session, AuditLog, audit_event_canonical_parts, "ecom-app")


def test_audit_chain_tamper_detected(session):
    for i in range(3):
        record_audit_event(
            session, event_type="account_op", event_action="test.op",
            summary=f"op {i}", actor_source="cli", account_id="ecom-app",
        )
    session.flush()
    mid = session.query(AuditLog).order_by(AuditLog.id).all()[1]
    mid.summary = "TAMPERED"  # 改内容但不重算 hash → 链校验应失败
    session.flush()
    assert not _verify_chain(session, AuditLog, audit_event_canonical_parts, "ecom-app")


def test_reseal_chain_repairs_after_account_merge(session):
    """并租户后重新封链：改 account_id（进哈希→等同篡改）断链，reseal 用当前 canonical 重封即完好。

    复刻 scripts/migrate_merge_gtl_into_ecom_hp 的场景：gtl 审计行改归 ecom-app，与 ecom-app
    原有行按 id 交织 → verify 报断裂；reseal_chain(ecom-app) 后两条链自洽。
    """
    # ecom-app 与 gtl 交替各记数条 API 调用，使 id 交织。
    for i in range(3):
        record_api_call(session, category="business", method="GET", path=f"/e{i}",
                        account_id="ecom-app", http_status=200, ok=True)
        record_api_call(session, category="business", method="GET", path=f"/g{i}",
                        account_id="ecom-app-gtl", http_status=200, ok=True)
    session.flush()
    assert verify_chain(session, ApiCallLog, api_call_canonical_parts) == []  # 迁移前完好

    # 模拟迁移的盲改：把 gtl 行 account_id 直接改成 ecom-app（不重算 hash）。
    session.query(ApiCallLog).filter(ApiCallLog.account_id == "ecom-app-gtl").update(
        {ApiCallLog.account_id: "ecom-app"}, synchronize_session=False
    )
    session.flush()
    session.expire_all()
    breaks = verify_chain(session, ApiCallLog, api_call_canonical_parts)
    assert breaks, "改 account_id 后应检出断裂"
    assert all(b["account_id"] == "ecom-app" for b in breaks)

    # 重新封链后应完好，且行数不变（只改 prev_hash/row_hash 两列）。
    n = reseal_chain(session, ApiCallLog, api_call_canonical_parts, "ecom-app")
    session.flush()
    assert n == 6
    assert verify_chain(session, ApiCallLog, api_call_canonical_parts) == []
    rows = session.query(ApiCallLog).order_by(ApiCallLog.id).all()
    assert rows[0].prev_hash is None  # 重封后链首指针归零
    # created_at 等被哈希业务字段未被 reseal 改动（否则等于二次篡改内容）。
    assert {r.path for r in rows} == {f"/e{i}" for i in range(3)} | {f"/g{i}" for i in range(3)}


def test_chain_per_account_isolated(session):
    record_audit_event(session, event_type="account_op", event_action="a",
                       summary="A1", actor_source="cli", account_id="ecom-app")
    record_audit_event(session, event_type="account_op", event_action="b",
                       summary="B1", actor_source="cli", account_id="ecom-app-gtl")
    session.flush()
    a = session.query(AuditLog).filter(AuditLog.account_id == "ecom-app").one()
    b = session.query(AuditLog).filter(AuditLog.account_id == "ecom-app-gtl").one()
    assert a.prev_hash is None and b.prev_hash is None  # 两租户各自链首
    assert a.row_hash != b.row_hash


def test_api_call_chain_and_failure_row(session):
    record_api_call(session, category="business", method="POST",
                    path="/product/202309/products/search", account_id="ecom-app",
                    http_status=200, business_code=0, ok=True)
    record_api_call(session, category="business", method="GET",
                    path="/order/202309/orders/search", account_id="ecom-app",
                    http_status=500, business_code=None, ok=False, error="boom")
    session.flush()
    assert _verify_chain(session, ApiCallLog, api_call_canonical_parts, "ecom-app")
    rows = session.query(ApiCallLog).order_by(ApiCallLog.id).all()
    assert rows[1].ok is False and rows[1].error == "boom"


def test_error_truncated_to_500(session):
    rec = record_api_call(session, category="business", method="GET", path="/x",
                          account_id="ecom-app", ok=False, error="E" * 800)
    session.flush()
    assert len(rec.error) == 500


# ── scripts/verify_audit_chain + flows/anchor_audit_chain 的核心（services.audit）──

def test_verify_chain_clean_and_break(session):
    """verify_chain：完好返回空、跨租户篡改返回断裂行（定位到 table/account_id/id）。"""
    for acc in ("ecom-app", "ecom-app-gtl"):
        for i in range(3):
            record_audit_event(session, event_type="account_op", event_action="op",
                               summary=f"{acc} {i}", actor_source="cli", account_id=acc)
    session.flush()
    assert verify_chain(session, AuditLog, audit_event_canonical_parts) == []  # 两租户链均完好

    victim = (
        session.query(AuditLog)
        .filter(AuditLog.account_id == "ecom-app-gtl")
        .order_by(AuditLog.id)
        .all()[1]
    )
    victim.summary = "TAMPERED"  # 改内容不重算 hash
    session.flush()
    breaks = verify_chain(session, AuditLog, audit_event_canonical_parts)
    assert [b["account_id"] for b in breaks] == ["ecom-app-gtl"]  # 仅被改租户链断
    assert breaks[0]["table"] == "audit_log" and breaks[0]["id"] == victim.id


def test_chain_tips_per_tenant(session):
    """chain_tips：按租户给链尾 {count, tip_id, tip_hash}，tip_hash 即该租户最后一行 row_hash。"""
    for i in range(2):
        record_audit_event(session, event_type="account_op", event_action="a",
                           summary=f"A{i}", actor_source="cli", account_id="ecom-app")
    record_audit_event(session, event_type="account_op", event_action="b",
                       summary="B0", actor_source="cli", account_id="ecom-app-gtl")
    session.flush()

    tips = {t["account_id"]: t for t in chain_tips(session, AuditLog)}
    assert tips["ecom-app"]["count"] == 2 and tips["ecom-app-gtl"]["count"] == 1
    last = (
        session.query(AuditLog).filter(AuditLog.account_id == "ecom-app")
        .order_by(AuditLog.id).all()[-1]
    )
    assert tips["ecom-app"]["tip_id"] == last.id
    assert tips["ecom-app"]["tip_hash"] == last.row_hash


def test_anchor_build_message(session):
    """anchor flow 的 _build_message：含两表完整性行 + 链尾，篡改时 break 计数>0 并附告警。"""
    from flows.anchor_audit_chain import _build_message

    record_audit_event(session, event_type="account_op", event_action="op",
                       summary="x", actor_source="cli", account_id="ecom-app")
    record_api_call(session, category="business", method="GET", path="/y",
                    account_id="ecom-app", http_status=200, business_code=0, ok=True)
    session.flush()

    msg, breaks = _build_message(session)
    assert breaks == 0
    assert "audit_log: 完好" in msg and "api_call_logs: 完好" in msg
    assert "ecom-app" in msg and "🔒 审计链每日锚定" in msg

    rec = session.query(AuditLog).filter(AuditLog.account_id == "ecom-app").one()
    rec.summary = "TAMPERED"
    session.flush()
    msg2, breaks2 = _build_message(session)
    assert breaks2 == 1 and "断裂" in msg2 and "⚠️" in msg2


def test_before_after_in_chain_tamper_detected(session):
    """权限变更证据 before/after_json 已纳入哈希链：静默改 after_json（如反向洗白提权）即断链。"""
    record_audit_event(
        session, event_type="authz_change", event_action="role.upsert",
        actor_open_id="ou_boss", actor_source="web", target="ou_victim",
        summary="改权限", before={"role": "operator"}, after={"role": "boss"},
        account_id="ecom-app",
    )
    session.flush()
    rec = session.query(AuditLog).filter(AuditLog.account_id == "ecom-app").one()
    assert verify_chain(session, AuditLog, audit_event_canonical_parts) == []
    rec.after_json = {"role": "operator"}  # 洗白：把提权记录改成没提权，不重算 hash
    session.flush()
    breaks = verify_chain(session, AuditLog, audit_event_canonical_parts)
    assert [b["id"] for b in breaks] == [rec.id]  # after_json 入链 → 篡改被检出


def test_canonical_no_separator_collision(session):
    """canonical 长度前缀编码：字段含分隔符也不碰撞（旧 `|` join 会把这两组拼成同串）。"""
    assert compute_row_hash(None, ["a|b", "c"]) != compute_row_hash(None, ["a", "b|c"])
    assert compute_row_hash(None, ["", "x"]) != compute_row_hash(None, ["x", ""])
