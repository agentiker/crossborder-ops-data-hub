"""Web 会话持久化 DAO 单测（plan/15 Phase A）：CRUD + 归属隔离。"""

import pytest

from services import web_conversation_store as store


@pytest.fixture()
def _db(session, monkeypatch):
    monkeypatch.setattr(store, "SessionLocal", lambda: session)
    return session


def test_create_and_list(_db):
    cid = store.create_conversation("ou_a", title="第一个")
    assert cid > 0
    items = store.list_conversations("ou_a")
    assert len(items) == 1 and items[0]["title"] == "第一个"


def test_messages_roundtrip(_db):
    cid = store.create_conversation("ou_a")
    store.append_message(cid, "user", "今天GMV多少")
    store.append_message(cid, "assistant", "100 万", [{"name": "ops_orders_summary", "ok": True}])
    msgs = store.get_messages(cid, "ou_a")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["tool_calls"] == [{"name": "ops_orders_summary", "ok": True}]


def test_ownership_isolation(_db):
    cid = store.create_conversation("ou_a", title="A的会话")
    # 别人看不到、取不到、改不动、删不掉
    assert store.list_conversations("ou_b") == []
    assert store.get_conversation(cid, "ou_b") is None
    assert store.get_messages(cid, "ou_b") == []
    assert store.rename_conversation(cid, "ou_b", "改名") is False
    assert store.delete_conversation(cid, "ou_b") is False
    # 本人可以
    assert store.rename_conversation(cid, "ou_a", "新名") is True
    assert store.get_conversation(cid, "ou_a")["title"] == "新名"
    assert store.delete_conversation(cid, "ou_a") is True
    assert store.get_conversation(cid, "ou_a") is None


def test_delete_removes_messages(_db):
    cid = store.create_conversation("ou_a")
    store.append_message(cid, "user", "hi")
    assert store.delete_conversation(cid, "ou_a") is True
    assert store.get_messages(cid, "ou_a") == []
