"""请求级审计身份上下文（plan 审计合规第 3 节）——单一事实源。

API 调用审计需要记录「谁触发了这次调用」，但 platforms client 只知 account_id/shop_id/
scope_key，不知 actor（飞书 open_id）、来源（web/cli/timer/oauth）、request_id。本模块用
contextvar（与 core/tenancy 同范式）把这些从请求入口传到深层 client，零签名改动。

注入点（见各入口）：
- web：async 依赖 bind_audit_context（web/security）从 cookie/perm 取 open_id + source="web"。
- 定时任务：flow 入口 set_audit_actor(open_id="system", source="timer")。
- OAuth：回调内 set_audit_actor(source="oauth")。
- CLI：脚本入口 set_audit_actor(open_id=args.operator or os.getenv("USER"), source="cli")。

ContextVar 默认空 dict；set 总是基于拷贝构造新 dict 再 set（绝不原地改默认对象）。
"""
from __future__ import annotations

import contextvars
from typing import Optional

_audit_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "audit_ctx", default={}
)


def set_audit_actor(
    *,
    open_id: Optional[str] = None,
    source: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    """合并写入当前请求的审计身份（须在 async 依赖里调，threadpool 的 set 不回传父 context）。"""
    cur = dict(_audit_ctx.get())
    if open_id is not None:
        cur["open_id"] = open_id
    if source is not None:
        cur["source"] = source
    if request_id is not None:
        cur["request_id"] = request_id
    _audit_ctx.set(cur)


def current_audit_actor() -> dict:
    """读当前请求审计身份的拷贝：{open_id?, source?, request_id?}（可能为空 dict）。"""
    return dict(_audit_ctx.get())
