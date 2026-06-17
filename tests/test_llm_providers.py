"""LLM Provider 适配器流式解析单测（plan/15 Phase A）。

monkeypatch requests.post 喂假 SSE，验证两类适配器把流式响应正确还原成
TextDelta + TurnComplete（含工具调用拼装），不依赖真实模型。
"""

import pytest

from services.llm import anthropic as anth_mod
from services.llm import openai_compat as oai_mod
from services.llm.anthropic import AnthropicProvider
from services.llm.openai_compat import OpenAICompatProvider
from services.llm.types import TextDelta, TurnComplete


class _FakeResp:
    def __init__(self, lines, status=200, text=""):
        self._lines = lines
        self.status_code = status
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)


def _drain(gen):
    deltas, final = [], None
    for ev in gen:
        if isinstance(ev, TextDelta):
            deltas.append(ev.text)
        elif isinstance(ev, TurnComplete):
            final = ev
    return deltas, final


def _p_openai():
    return OpenAICompatProvider(base_url="https://x/v1", api_key="k", model="m")


def _p_anthropic():
    return AnthropicProvider(base_url="https://x", api_key="k", model="m")


# ── OpenAI 兼容 ──────────────────────────────────────────────────────────────


def test_openai_text_stream(monkeypatch):
    lines = [
        'data: {"choices":[{"delta":{"content":"你好"}}]}',
        'data: {"choices":[{"delta":{"content":"世界"}}]}',
        'data: {"choices":[{"finish_reason":"stop","delta":{}}]}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(oai_mod.requests, "post", lambda *a, **k: _FakeResp(lines))
    deltas, final = _drain(_p_openai().stream([], []))
    assert deltas == ["你好", "世界"]
    assert final.text == "你好世界"
    assert final.finish_reason == "stop"
    assert final.tool_calls == []


def test_openai_tool_call_stream(monkeypatch):
    lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"ops_overview","arguments":""}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{}"}}]}}]}',
        'data: {"choices":[{"finish_reason":"tool_calls","delta":{}}]}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(oai_mod.requests, "post", lambda *a, **k: _FakeResp(lines))
    _, final = _drain(_p_openai().stream([], []))
    assert final.finish_reason == "tool_calls"
    assert len(final.tool_calls) == 1
    tc = final.tool_calls[0]
    assert tc.name == "ops_overview" and tc.arguments == {} and tc.id == "call_1"


def test_openai_non_200_raises(monkeypatch):
    monkeypatch.setattr(oai_mod.requests, "post",
                        lambda *a, **k: _FakeResp([], status=401, text="bad key"))
    from services.llm.types import LLMError
    with pytest.raises(LLMError):
        _drain(_p_openai().stream([], []))


# ── Anthropic ────────────────────────────────────────────────────────────────


def test_anthropic_text_stream(monkeypatch):
    lines = [
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"早上"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"好"}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
        'data: {"type":"message_stop"}',
    ]
    monkeypatch.setattr(anth_mod.requests, "post", lambda *a, **k: _FakeResp(lines))
    deltas, final = _drain(_p_anthropic().stream([], []))
    assert deltas == ["早上", "好"]
    assert final.text == "早上好"
    assert final.finish_reason == "stop"


def test_anthropic_tool_call_stream(monkeypatch):
    lines = [
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_1","name":"ops_top_skus"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"period\\":"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"\\"last_7d\\"}"}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}',
        'data: {"type":"message_stop"}',
    ]
    monkeypatch.setattr(anth_mod.requests, "post", lambda *a, **k: _FakeResp(lines))
    _, final = _drain(_p_anthropic().stream([], []))
    assert final.finish_reason == "tool_calls"
    assert len(final.tool_calls) == 1
    tc = final.tool_calls[0]
    assert tc.name == "ops_top_skus" and tc.arguments == {"period": "last_7d"}
