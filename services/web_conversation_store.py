"""Web 对话会话持久化 DAO（plan/15 Phase A）。

所有读写都按 open_id 夹住归属——一个用户只能看/改自己的会话（与权限闸同向）。
表定义见 models.base_models.WebConversation / WebMessage。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.sql import func

from core.db import SessionLocal
from models.base_models import WebConversation, WebMessage


def create_conversation(open_id: str, title: str = "新会话") -> int:
    """新建会话，返回 id。"""
    session = SessionLocal()
    try:
        conv = WebConversation(open_id=open_id, title=title[:200] or "新会话")
        session.add(conv)
        session.commit()
        return conv.id
    finally:
        session.close()


def list_conversations(open_id: str, limit: int = 50) -> list[dict]:
    """按最近更新倒序列出该用户的会话。"""
    session = SessionLocal()
    try:
        rows = (
            session.query(WebConversation)
            .filter(WebConversation.open_id == open_id)
            .order_by(WebConversation.updated_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "title": r.title,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
    finally:
        session.close()


def get_conversation(conversation_id: int, open_id: str) -> Optional[dict]:
    """取单个会话（带归属校验）。非本人/不存在 → None。"""
    session = SessionLocal()
    try:
        r = (
            session.query(WebConversation)
            .filter(
                WebConversation.id == conversation_id,
                WebConversation.open_id == open_id,
            )
            .first()
        )
        if r is None:
            return None
        return {"id": r.id, "title": r.title, "open_id": r.open_id}
    finally:
        session.close()


def get_messages(conversation_id: int, open_id: str) -> list[dict]:
    """取某会话的全部消息（升序）。先校验归属，非本人返回空。"""
    if get_conversation(conversation_id, open_id) is None:
        return []
    session = SessionLocal()
    try:
        rows = (
            session.query(WebMessage)
            .filter(WebMessage.conversation_id == conversation_id)
            .order_by(WebMessage.id.asc())
            .all()
        )
        return [
            {
                "id": r.id,
                "role": r.role,
                "content": r.content,
                "tool_calls": r.tool_calls_json,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        session.close()


def append_message(
    conversation_id: int,
    role: str,
    content: str,
    tool_calls_json: Optional[list] = None,
) -> int:
    """追加一条消息并把会话 updated_at 顶到最新，返回消息 id。"""
    session = SessionLocal()
    try:
        msg = WebMessage(
            conversation_id=conversation_id,
            role=role,
            content=content or "",
            tool_calls_json=tool_calls_json,
        )
        session.add(msg)
        # 触碰会话使其在列表中置顶（显式刷新 updated_at）
        conv = session.get(WebConversation, conversation_id)
        if conv is not None:
            conv.updated_at = func.now()
        session.commit()
        return msg.id
    finally:
        session.close()


def rename_conversation(conversation_id: int, open_id: str, title: str) -> bool:
    """重命名（带归属校验）。成功 True。"""
    session = SessionLocal()
    try:
        r = (
            session.query(WebConversation)
            .filter(
                WebConversation.id == conversation_id,
                WebConversation.open_id == open_id,
            )
            .first()
        )
        if r is None:
            return False
        r.title = (title or "").strip()[:200] or r.title
        session.commit()
        return True
    finally:
        session.close()


def delete_conversation(conversation_id: int, open_id: str) -> bool:
    """删除会话及其消息（带归属校验）。成功 True。"""
    session = SessionLocal()
    try:
        r = (
            session.query(WebConversation)
            .filter(
                WebConversation.id == conversation_id,
                WebConversation.open_id == open_id,
            )
            .first()
        )
        if r is None:
            return False
        session.query(WebMessage).filter(
            WebMessage.conversation_id == conversation_id
        ).delete()
        session.delete(r)
        session.commit()
        return True
    finally:
        session.close()
