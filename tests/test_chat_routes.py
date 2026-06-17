"""Web 对话路由（/api/chat 等）单测（plan/15 Phase A）。

用假 Provider + 假 run_tool + 内存 sqlite 隔离掉真实 LLM/DB，验证：鉴权、SSE 事件、
会话落库、工具调用把登录身份(perm)正确传给工具层（范围夹紧的入口）。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.config import settings
from core.db import Base
from models import base_models  # noqa: F401  注册 ORM 表到 Base.metadata
from services import web_conversation_store as store
from services.llm.types import TextDelta, ToolCall, TurnComplete
from services.user_authz import UserPermission
from web import routes as _routes  # noqa: F401  确保包已加载
from web.app import app
from web.routes import chat as chat_mod
from web.web_security import require_web_user_api

BOSS = UserPermission(open_id="ou_boss", role="boss", allowed_scope_key=None,
                      channel="feishu", account_id="ecom-app")
OPER = UserPermission(open_id="ou_op", role="operator", allowed_scope_key="scope-a",
                      channel="feishu", account_id="ecom-app")


class _FakeProvider:
    """按脚本回放多轮：每个 script = {deltas?, text?, tool_calls?}。"""

    def __init__(self, scripts):
        self.scripts = scripts
        self.i = 0

    def stream(self, messages, tools):
        script = self.scripts[min(self.i, len(self.scripts) - 1)]
        self.i += 1
        for d in script.get("deltas", []):
            yield TextDelta(d)
        tcs = script.get("tool_calls", [])
        yield TurnComplete(
            text=script.get("text", ""),
            tool_calls=tcs,
            finish_reason="tool_calls" if tcs else "stop",
        )


@pytest.fixture()
def _db(monkeypatch):
    # chat 路由的流式生成器跑在 Starlette 线程池里，故用 StaticPool + check_same_thread=False
    # 共享单个 sqlite 内存连接跨线程（默认 SingletonThreadPool 会给新线程空白库 → no such table）。
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(store, "SessionLocal", Session)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _clear():
    yield
    app.dependency_overrides.clear()


def _parse_sse(text):
    """解析 SSE 文本为 [(event, data_str), ...]。"""
    events = []
    cur_ev = None
    for line in text.splitlines():
        if line.startswith("event:"):
            cur_ev = line[len("event:"):].strip()
        elif line.startswith("data:"):
            events.append((cur_ev, line[len("data:"):].strip()))
    return events


def test_chat_requires_auth():
    # 无 cookie、无 override → require_web_user_api 返回 401
    r = TestClient(app).post("/api/chat", json={"message": "hi"})
    assert r.status_code == 401


def test_chat_streams_tool_then_answer_and_persists(_db, monkeypatch):
    app.dependency_overrides[require_web_user_api] = lambda: BOSS
    # 第一轮：模型要调一个工具；第二轮：给文字答案
    fake = _FakeProvider([
        {"tool_calls": [ToolCall(id="c1", name="ops_overview", arguments={})]},
        {"deltas": ["近7天", "GMV 100万"], "text": "近7天GMV 100万"},
    ])
    monkeypatch.setattr(chat_mod, "get_provider", lambda: fake)
    monkeypatch.setattr(chat_mod, "run_tool", lambda name, args, perm: {"echo": name})

    r = TestClient(app).post("/api/chat", json={"message": "整体情况怎样"})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "meta"
    assert "tool" in kinds
    assert "delta" in kinds
    assert kinds[-1] == "done"

    # 落库：user + assistant，且 assistant 带工具审计
    import json
    meta = json.loads(events[0][1])
    cid = meta["conversation_id"]
    msgs = store.get_messages(cid, "ou_boss")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert "100万" in msgs[1]["content"]
    assert msgs[1]["tool_calls"] == [{"name": "ops_overview", "arguments": {}, "ok": True}]


def test_chat_passes_login_perm_to_tool(_db, monkeypatch):
    app.dependency_overrides[require_web_user_api] = lambda: OPER
    captured = {}
    fake = _FakeProvider([
        {"tool_calls": [ToolCall(id="c1", name="ops_orders_summary", arguments={"period": "today"})]},
        {"text": "好的"},
    ])
    monkeypatch.setattr(chat_mod, "get_provider", lambda: fake)

    def fake_run_tool(name, args, perm):
        captured["perm"] = perm
        captured["args"] = args
        return {"ok": True}

    monkeypatch.setattr(chat_mod, "run_tool", fake_run_tool)
    r = TestClient(app).post("/api/chat", json={"message": "今天卖了多少"})
    assert r.status_code == 200
    # 登录身份(operator)被原样传给工具层 → 范围夹紧由 user_authz 在工具层执行
    assert captured["perm"].role == "operator"
    assert captured["perm"].allowed_scope_key == "scope-a"
    # 模型传的是 period，不含任何 scope/shop 参数
    assert captured["args"] == {"period": "today"}


def test_chat_empty_message_400(_db, monkeypatch):
    app.dependency_overrides[require_web_user_api] = lambda: BOSS
    r = TestClient(app).post("/api/chat", json={"message": "  "})
    assert r.status_code == 400


def test_me_returns_role(_db, monkeypatch):
    app.dependency_overrides[require_web_user_api] = lambda: BOSS
    monkeypatch.setattr(chat_mod, "_scope_label", lambda perm: "全部范围")
    r = TestClient(app).get("/api/me")
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "boss" and body["is_boss"] is True
