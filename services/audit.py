"""审计服务底座 + 哈希链（plan 审计合规第 2 节）。

三类合规审计的统一写入层，照 services/sync_state.record_raw_response 的 append-only 范式：
- record_api_call：API 调用审计（client 两单点插桩，category=business/auth_token/oauth）。
- record_audit_event：账号操作 / 权限变更 / 授权记录（event_type 区分）。

**不可篡改 = 哈希链（按 account_id 分链）**：每行 row_hash = sha256(prev_hash | 业务字段),
prev_hash = 同租户上一行 row_hash。任意单行被改 → 其后所有 row_hash 对不上即断链可检测。
按 account_id 分链而非全局单链，是为与 core/db.py 的 ORM 自动租户过滤天然契合（读链尾时
本就只该看本租户），且并发争用只在同租户内。链尾每日经 flows/anchor_audit_chain 发飞书留痕
→ 删尾会与昨日锚点对不上 → 不可抵赖。校验见 scripts/verify_audit_chain。

**读链尾用 raw SQL + FOR UPDATE**：绕过 do_orm_execute 的 with_loader_criteria 注入（否则
contextvar 租户与目标 account 不一致时会 AND 出错），显式 filter account_id 锁定本租户链尾。

**写入失败绝不阻断业务**：record_* 是纯函数（传 session）；独立场景用 log_*_safe（自开短
session + 吞错）。调用方务必用 try/except 包裹同事务调用，审计是旁路、可用性优先。
"""
from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

from core.audit_context import current_audit_actor
from core.db import SessionLocal
from core.tenancy import current_account
from models.base_models import ApiCallLog, AuditLog

logger = logging.getLogger(__name__)


def _utcnow_sec() -> datetime:
    """naive UTC 秒级时间戳。秒级（非 μs）以匹配 MySQL DATETIME 默认精度——否则 verify
    重算 isoformat() 会因 DB 截断小数秒而与 hash 不符。同秒多行靠 prev_hash 区分 row_hash。"""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _json_canon(v: Any) -> str:
    """权限变更 before/after 的稳定序列化（sort_keys 让键序无关），供入哈希链。None→""。"""
    if v is None:
        return ""
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _row_hash(prev_hash: Optional[str], parts: list[str]) -> str:
    """长度前缀编码后 sha256。用 `len:value` 拼接而非 `|` join——否则字段含分隔符时
    ({target:"a|b",summary:"c"} 与 {target:"a",summary:"b|c"}) 会拼出同串→碰撞，攻击者
    可构造哈希等价的替身内容做篡改而不断链。长度前缀消除字段边界歧义。"""
    items = [prev_hash or ""] + [p or "" for p in parts]
    canonical = "".join(f"{len(s.encode('utf-8'))}:{s}" for s in items)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _append_with_chain(session, model, account_id: Optional[str], fields: dict,
                       canonical_parts: list[str], created_at: datetime):
    """读同租户链尾（raw SQL + FOR UPDATE）→ 算 row_hash → INSERT+flush。

    FOR UPDATE 仅在支持行锁的方言（MySQL）加：单机串行追加同一租户链时靠它锁住链尾防并发
    分叉。sqlite（测试 in-memory）单连接无并发、且不支持该语法，故按方言省略。
    """
    acc = account_id or ""
    lock = " FOR UPDATE" if session.bind.dialect.name == "mysql" else ""
    row = session.execute(
        text(
            f"SELECT row_hash FROM {model.__tablename__} "
            f"WHERE account_id = :acc ORDER BY id DESC LIMIT 1{lock}"
        ),
        {"acc": acc},
    ).first()
    prev_hash = row[0] if row else None
    rh = _row_hash(prev_hash, canonical_parts)
    rec = model(**fields, created_at=created_at, prev_hash=prev_hash, row_hash=rh)
    session.add(rec)
    session.flush()
    return rec


def record_api_call(
    session,
    *,
    category: str,
    method: str,
    path: str,
    account_id: Optional[str] = None,
    platform: str = "tiktok_shop",
    scope_key: Optional[str] = None,
    shop_id: Optional[str] = None,
    http_status: Optional[int] = None,
    business_code: Optional[Any] = None,
    ok: bool = True,
    error: Optional[str] = None,
    duration_ms: Optional[int] = None,
    actor: Optional[dict] = None,
    request_id: Optional[str] = None,
) -> ApiCallLog:
    """记一条 API 调用审计（纯函数，传 session）。"""
    actor = actor if actor is not None else current_audit_actor()
    acc = account_id if account_id is not None else current_account()
    created_at = _utcnow_sec()
    bcode = str(business_code) if business_code is not None else None
    fields = dict(
        account_id=acc,
        platform=platform,
        scope_key=scope_key,
        shop_id=shop_id,
        category=category,
        method=(method or "").upper(),
        path=path,
        http_status=http_status,
        business_code=bcode,
        ok=ok,
        error=(error or "")[:500] or None,
        duration_ms=duration_ms,
        actor_open_id=actor.get("open_id"),
        actor_source=actor.get("source"),
        request_id=request_id or actor.get("request_id"),
    )
    parts = [
        acc or "", category, fields["method"], path or "",
        scope_key or "", shop_id or "",
        str(http_status or ""), bcode or "", "1" if ok else "0",
        actor.get("open_id") or "", created_at.isoformat(),
    ]
    return _append_with_chain(session, ApiCallLog, acc, fields, parts, created_at)


def record_audit_event(
    session,
    *,
    event_type: str,
    event_action: str,
    actor_open_id: Optional[str] = None,
    actor_source: Optional[str] = None,
    target: Optional[str] = None,
    summary: Optional[str] = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    account_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> AuditLog:
    """记一条账号操作/权限变更/授权事件（纯函数，传 session）。权限变更须填 before/after。"""
    actor = current_audit_actor()
    acc = account_id if account_id is not None else current_account()
    actor_open_id = actor_open_id if actor_open_id is not None else actor.get("open_id")
    actor_source = actor_source if actor_source is not None else actor.get("source")
    created_at = _utcnow_sec()
    summary = (summary or "")[:500]
    fields = dict(
        account_id=acc,
        event_type=event_type,
        event_action=event_action,
        actor_open_id=actor_open_id,
        actor_source=actor_source,
        target=target,
        summary=summary,
        before_json=before,
        after_json=after,
        request_id=request_id or actor.get("request_id"),
    )
    parts = [
        acc or "", event_type, event_action, actor_open_id or "", actor_source or "",
        target or "", summary or "",
        _json_canon(before), _json_canon(after),
        created_at.isoformat(),
    ]
    return _append_with_chain(session, AuditLog, acc, fields, parts, created_at)


@contextmanager
def _audit_session():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def log_api_call_safe(**kwargs) -> None:
    """自开短 session 记 API 调用 + 吞掉一切异常（client 等独立场景用，绝不阻断业务）。"""
    try:
        with _audit_session() as s:
            record_api_call(s, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.error("[audit] record_api_call 失败（已吞）: %s", exc)


def log_audit_event_safe(**kwargs) -> None:
    """自开短独立 session 记审计事件 + 吞错。

    **仅供没有现成业务 session 的场景**：OAuth 回调（auth.py）、token 刷新 flow。它连的是
    DATABASE_URL（生产/hp 的 MySQL），与调用方事务无关。web 写端点/CLI **不要**用它（用
    record_audit_event_safe 走业务 session）——否则测试里它会绕过 fixture 的 session patch、
    直写真实 hp 库，把测试垃圾灌进不可篡改哈希链。"""
    try:
        with _audit_session() as s:
            record_audit_event(s, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.error("[audit] record_audit_event 失败（已吞）: %s", exc)


def record_audit_event_safe(session, **kwargs) -> None:
    """在**业务 session** 上记审计事件 + 吞错（web 写端点/CLI 用）。

    须在业务自身 commit **之后**调用：本函数 record_audit_event(flush) 后再 commit 一次，
    使审计成为业务之后的独立小事务——业务已落地，审计失败只回滚审计、不波及业务（避免在不可
    篡改链里留下"业务回滚了却记成功"的假记录），失败仅 logger.error 不抛（绝不阻断业务）。"""
    try:
        record_audit_event(session, **kwargs)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.error("[audit] record_audit_event_safe 失败（已吞）: %s", exc)


# 哈希链规范串的复算入口，供 scripts/verify_audit_chain 重算比对（单一事实源）。
def api_call_canonical_parts(rec: ApiCallLog) -> list[str]:
    return [
        rec.account_id or "", rec.category, (rec.method or "").upper(), rec.path or "",
        rec.scope_key or "", rec.shop_id or "",
        str(rec.http_status or ""), rec.business_code or "", "1" if rec.ok else "0",
        rec.actor_open_id or "", rec.created_at.isoformat() if rec.created_at else "",
    ]


def audit_event_canonical_parts(rec: AuditLog) -> list[str]:
    return [
        rec.account_id or "", rec.event_type, rec.event_action, rec.actor_open_id or "",
        rec.actor_source or "",
        rec.target or "", rec.summary or "",
        _json_canon(rec.before_json), _json_canon(rec.after_json),
        rec.created_at.isoformat() if rec.created_at else "",
    ]


def compute_row_hash(prev_hash: Optional[str], parts: list[str]) -> str:
    """供 verify 脚本复算（与写入同一算法）。"""
    return _row_hash(prev_hash, parts)


# 两条审计链的 (模型, 规范串函数) 清单，供锚定/校验统一遍历（单一事实源）。
CHAIN_MODELS = [
    (ApiCallLog, api_call_canonical_parts),
    (AuditLog, audit_event_canonical_parts),
]


def chain_tips(session, model) -> list[dict]:
    """每租户（account_id）链尾摘要 {account_id, count, tip_id, tip_hash}。

    供 flows/anchor_audit_chain 外发留痕。跨租户读：调用方须先 set_current_account(TENANT_BYPASS)。
    """
    from sqlalchemy import func as _func

    grouped = (
        session.query(model.account_id, _func.count(model.id), _func.max(model.id))
        .group_by(model.account_id)
        .all()
    )
    tips = []
    for acc, cnt, max_id in grouped:
        tip = session.get(model, max_id) if max_id is not None else None
        tips.append({
            "account_id": acc, "count": cnt, "tip_id": max_id,
            "tip_hash": tip.row_hash if tip else None,
        })
    return tips


def verify_chain(session, model, canonical_fn) -> list[dict]:
    """重算整表所有租户链，返回断裂行列表（空=完好）。

    每行用「实际前一行 row_hash + 本行规范串」复算 row_hash，与存库值比对；prev_hash 指针也校验。
    跨租户读：调用方须先 set_current_account(TENANT_BYPASS)。供 scripts/verify_audit_chain。
    """
    rows = session.query(model).order_by(model.account_id, model.id).all()
    prev_by_acc: dict = {}
    breaks = []
    for r in rows:
        prev = prev_by_acc.get(r.account_id)
        expected = compute_row_hash(prev, canonical_fn(r))
        if r.prev_hash != prev or r.row_hash != expected:
            breaks.append({
                "table": model.__tablename__, "account_id": r.account_id, "id": r.id,
            })
        prev_by_acc[r.account_id] = r.row_hash
    return breaks
