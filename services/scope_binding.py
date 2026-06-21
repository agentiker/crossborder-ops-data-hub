"""会话级"默认查询范围"持久绑定（conversation_scope_bindings）。

跨会话记住用户上次通过菜单切换的范围：
- `set_binding`：菜单切换默认范围时写入（agent 经 ops_set_scope_binding 调）。
- `get_binding`：数据端点（web/routes/data.py `_resolve_scope`）服务端自动读取注入，
  agent 不直接调（无读工具）。读写都走默认 channel/account_id，命中同一行。

校验与展示文案复用 `services/scope_resolution`：写入时非空 scope_key 必须存在且 active，
否则抛 `ScopeError`（API 层转 400）；`scope_key=None` 表示"显式全量"，合法。

单租户阶段不引入 tenant_id（见 plan/09）。本服务不接收任何自然语言。
"""
from __future__ import annotations

from typing import Optional

from core.db import SessionLocal
from core.tenancy import current_account
from models.base_models import ConversationScopeBinding
from services.scope_resolution import ScopeError, expand_scope

_ALL_SCOPE_DISPLAY = "全部范围"
_UNSET_DISPLAY = "未设置默认范围（全部）"


def _display_for(scope_key: Optional[str], account_id: str) -> str:
    """已设置 binding 的展示文案。scope_key 为空 = 显式全量。"""
    if not scope_key:
        return _ALL_SCOPE_DISPLAY
    # 复用 scope 展开拿权威 display_text（也顺带校验仍有效），按本租户找 scope。
    return expand_scope(scope_key, account_id=account_id).display_text


def get_binding(
    open_id: str,
    *,
    channel: str = "feishu",
    account_id: Optional[str] = None,
) -> dict:
    """读会话默认范围绑定。无绑定返回 is_set=False（agent 据此走全量）。

    多租户：account_id 默认取当前请求租户（X-Account-Id 头）；读写须同租户才命中同一行。
    """
    account_id = account_id or current_account()
    session = SessionLocal()
    try:
        row = (
            session.query(ConversationScopeBinding)
            .filter(
                ConversationScopeBinding.channel == channel,
                ConversationScopeBinding.account_id == account_id,
                ConversationScopeBinding.open_id == open_id,
            )
            .first()
        )
        if row is None:
            return {"scope_key": None, "scope": _UNSET_DISPLAY, "is_set": False}
        return {
            "scope_key": row.scope_key,
            "scope": _display_for(row.scope_key, account_id),
            "is_set": True,
        }
    finally:
        session.close()


def set_binding(
    open_id: str,
    scope_key: Optional[str],
    *,
    channel: str = "feishu",
    account_id: Optional[str] = None,
) -> dict:
    """写/更新会话默认范围绑定（upsert）。

    `scope_key` 非空时必须是已存在且启用的 scope（复用 expand_scope 校验，未知/停用抛
    ScopeError）；空字符串归一化为 None，表示"显式全量"。

    多租户：account_id 默认取当前请求租户（X-Account-Id 头）；与读端点同租户才命中同一行。
    """
    account_id = account_id or current_account()
    scope_key = scope_key or None
    if scope_key is not None:
        # 校验：未知/停用 scope 直接抛 ScopeError，绝不落脏 binding（按本租户找 scope）。
        expand_scope(scope_key, account_id=account_id)

    session = SessionLocal()
    try:
        row = (
            session.query(ConversationScopeBinding)
            .filter(
                ConversationScopeBinding.channel == channel,
                ConversationScopeBinding.account_id == account_id,
                ConversationScopeBinding.open_id == open_id,
            )
            .first()
        )
        if row is None:
            row = ConversationScopeBinding(
                channel=channel,
                account_id=account_id,
                open_id=open_id,
                scope_key=scope_key,
            )
            session.add(row)
        else:
            row.scope_key = scope_key
        session.commit()
        return {
            "scope_key": scope_key,
            "scope": _display_for(scope_key, account_id),
            "is_set": True,
        }
    finally:
        session.close()
