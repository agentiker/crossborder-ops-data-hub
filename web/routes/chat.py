"""Web 对话端：自建 agent loop + 会话管理 API（plan/15 Phase A）。

与飞书侧 openclaw 是两套 runtime，但共用同一权限闸（user_authz）+ 同一批 ops_* 取数
端点（web/agent_tools），口径与权限上限一致。鉴权用 plan/14 的飞书 OAuth 登录 cookie
（require_web_user_api，未登录返 401）。LLM 走可配置 Provider（services/llm）。

SSE 事件协议（前端 fetch 流式解析）：
- event: meta  data: {conversation_id, title}          —— 开头一次，告知会话 id
- event: delta data: {text}                            —— 文本增量
- event: tool  data: {name, status: running|ok|error}  —— 工具调用进度
- event: done  data: {conversation_id}                 —— 结束
- event: error data: {message}                         —— 出错（loop 内兜底）
"""

from __future__ import annotations

import json
import logging
from typing import Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.config import settings
from services.audit import log_audit_event_safe
from services.llm import ChatMessage, LLMError, TextDelta, TurnComplete, get_provider
from services.scope_resolution import ScopeError
from services.user_authz import AuthzError, UserPermission, resolve_authorized_scope
from services.web_conversation_store import (
    append_message,
    create_conversation,
    delete_conversation,
    get_conversation,
    get_messages,
    list_conversations,
    rename_conversation,
)
from web.agent_tools import TOOL_SPECS, run_tool
from web.web_security import require_web_user_api

logger = logging.getLogger(__name__)
router = APIRouter()


SYSTEM_PROMPT = (
    "你是跨境电商运营数据助手，服务 TikTok Shop 卖家团队。"
    "用简体中文、简洁专业地回答经营数据问题。\n"
    "你只能通过提供的工具读取数据，不要编造数字；没有数据就如实说没有。\n"
    "口径要点：GMV/订单/销量均为『已付款订单』口径，按印尼当地时间(UTC+7)归日；"
    "相对时间（今天/本周/近7天等）直接用工具的 period 参数，不要自己换算日期。\n"
    "查询范围已由系统按你的登录身份自动限定，你无需也无法指定店铺/范围。\n"
    "回答数据时优先给结论，再用 Markdown 表格列明细；金额标注币种（如有）。"
)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _scope_label(perm: UserPermission) -> str:
    try:
        return resolve_authorized_scope(perm).display_text
    except (ScopeError, AuthzError):
        return "（范围未配置）"


# ── 请求体 ───────────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[int] = None


class RenameRequest(BaseModel):
    title: str


# ── agent loop（同步生成器，跑在 Starlette 线程池里）─────────────────────────


def _run_agent(perm: UserPermission, conversation_id: int, user_message: str) -> Iterator[str]:
    """驱动一轮对话：流式吐文本 + 工具调用，最终把 assistant 回复落库。"""
    history = get_messages(conversation_id, perm.open_id)
    msgs: list[ChatMessage] = [ChatMessage(role="system", content=SYSTEM_PROMPT)]
    for h in history:
        if h["role"] in ("user", "assistant") and h.get("content"):
            msgs.append(ChatMessage(role=h["role"], content=h["content"]))
    msgs.append(ChatMessage(role="user", content=user_message))

    try:
        provider = get_provider()
    except LLMError as exc:
        yield _sse("error", {"message": str(exc)})
        return

    max_steps = settings.llm.max_tool_steps
    answer_parts: list[str] = []
    tool_audit: list[dict] = []

    try:
        for step in range(max_steps + 1):
            turn: Optional[TurnComplete] = None
            for ev in provider.stream(msgs, TOOL_SPECS):
                if isinstance(ev, TextDelta):
                    if ev.text:
                        yield _sse("delta", {"text": ev.text})
                elif isinstance(ev, TurnComplete):
                    turn = ev
            if turn is None:  # provider 没给收尾事件，保险退出
                break
            if turn.text:
                answer_parts.append(turn.text)
            msgs.append(ChatMessage(
                role="assistant", content=turn.text,
                tool_calls=turn.tool_calls or None,
            ))
            if not turn.tool_calls:
                break
            if step >= max_steps:
                note = "\n\n（已达工具调用步数上限，先汇报当前结果）"
                answer_parts.append(note)
                yield _sse("delta", {"text": note})
                break

            for tc in turn.tool_calls:
                yield _sse("tool", {"name": tc.name, "status": "running"})
                try:
                    data = run_tool(tc.name, tc.arguments, perm)
                    content = json.dumps(data, ensure_ascii=False, default=str)
                    ok = True
                except (ScopeError, AuthzError) as exc:
                    content = json.dumps({"error": "forbidden", "detail": str(exc)}, ensure_ascii=False)
                    ok = False
                except Exception as exc:  # noqa: BLE001 端点异常兜成给模型的观察
                    logger.exception("工具执行失败：%s", tc.name)
                    content = json.dumps({"error": "tool_failed", "detail": str(exc)}, ensure_ascii=False)
                    ok = False
                tool_audit.append({"name": tc.name, "arguments": tc.arguments, "ok": ok})
                msgs.append(ChatMessage(
                    role="tool", content=content, tool_call_id=tc.id, name=tc.name))
                yield _sse("tool", {"name": tc.name, "status": "ok" if ok else "error"})
    except LLMError as exc:
        yield _sse("error", {"message": f"模型调用失败：{exc}"})
        # 已有部分回答也落库，避免丢失
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent loop 异常")
        yield _sse("error", {"message": f"对话出错：{exc}"})

    answer = "\n".join(p for p in answer_parts if p).strip()
    append_message(conversation_id, "assistant", answer, tool_audit or None)
    yield _sse("done", {"conversation_id": conversation_id})


# ── 路由 ─────────────────────────────────────────────────────────────────────


@router.get("/api/me", include_in_schema=False)
async def me(perm: UserPermission = Depends(require_web_user_api)):
    """当前登录用户：身份 + 角色 + 被授权范围（前端顶栏展示）。"""
    return {
        "open_id": perm.open_id,
        "role": perm.role,
        "is_boss": perm.is_boss,
        "scope_label": _scope_label(perm),
    }


@router.get("/api/conversations", include_in_schema=False)
async def conversations(perm: UserPermission = Depends(require_web_user_api)):
    return {"items": list_conversations(perm.open_id)}


@router.get("/api/conversations/{conversation_id}", include_in_schema=False)
async def conversation_detail(
    conversation_id: int,
    perm: UserPermission = Depends(require_web_user_api),
):
    conv = get_conversation(conversation_id, perm.open_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"id": conv["id"], "title": conv["title"], "messages": get_messages(conversation_id, perm.open_id)}


@router.post("/api/conversations/{conversation_id}/rename", include_in_schema=False)
async def conversation_rename(
    conversation_id: int,
    body: RenameRequest,
    perm: UserPermission = Depends(require_web_user_api),
):
    if not rename_conversation(conversation_id, perm.open_id, body.title):
        raise HTTPException(status_code=404, detail="会话不存在")
    log_audit_event_safe(
        event_type="account_op", event_action="conversation.rename",
        actor_open_id=perm.open_id, actor_source="web", account_id=perm.account_id,
        target=str(conversation_id), summary="重命名会话", after={"title": body.title},
    )
    return {"ok": True}


@router.delete("/api/conversations/{conversation_id}", include_in_schema=False)
async def conversation_delete(
    conversation_id: int,
    perm: UserPermission = Depends(require_web_user_api),
):
    if not delete_conversation(conversation_id, perm.open_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    log_audit_event_safe(
        event_type="account_op", event_action="conversation.delete",
        actor_open_id=perm.open_id, actor_source="web", account_id=perm.account_id,
        target=str(conversation_id), summary="删除会话",
    )
    return {"ok": True}


@router.post("/api/chat", include_in_schema=False)
async def chat(body: ChatRequest, perm: UserPermission = Depends(require_web_user_api)):
    """发一条消息，SSE 流式返回 agent 回复。无 conversation_id 则新建会话。"""
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空")

    conv_id = body.conversation_id
    title = ""
    if conv_id is None:
        title = message[:30]
        conv_id = create_conversation(perm.open_id, title=title)
    else:
        conv = get_conversation(conv_id, perm.open_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        title = conv["title"]

    # 先落库用户消息，再开始流式
    append_message(conv_id, "user", message)
    log_audit_event_safe(  # 账号操作审计（不记消息内容，仅元数据）
        event_type="account_op", event_action="chat.message",
        actor_open_id=perm.open_id, actor_source="web", account_id=perm.account_id,
        target=str(conv_id), summary="发送对话消息",
    )

    def stream() -> Iterator[str]:
        yield _sse("meta", {"conversation_id": conv_id, "title": title})
        yield from _run_agent(perm, conv_id, message)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
