"""Anthropic Claude 适配器（plan/15 Phase A）。

Claude 用独立协议 /v1/messages（非 OpenAI 兼容）：system 是顶层参数、消息用
content block 数组、工具结果回灌为 user 消息里的 tool_result block。用 requests
直接打 SSE 流式，零 SDK 依赖。模型 id/默认值由配置决定（见 core.config.LLMConfig）。
"""

from __future__ import annotations

import json
import logging
from typing import Iterator

import requests

from services.llm.base import LLMProvider
from services.llm.types import (
    ChatMessage,
    LLMError,
    TextDelta,
    ToolCall,
    ToolSpec,
    TurnComplete,
)

logger = logging.getLogger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 4096


class AnthropicProvider(LLMProvider):
    """调用 Anthropic /v1/messages（stream=True）。"""

    def _url(self) -> str:
        base = self.base_url or "https://api.anthropic.com"
        base = base.rstrip("/")
        if base.endswith("/v1/messages"):
            return base
        return f"{base}/v1/messages"

    def _split(self, messages: list[ChatMessage]) -> tuple[str, list[dict]]:
        """拆出 system 文本 + 转成 Anthropic 消息（连续 tool 结果并进一条 user 消息）。"""
        system_parts: list[str] = []
        api_msgs: list[dict] = []
        for m in messages:
            if m.role == "system":
                if m.content:
                    system_parts.append(m.content)
            elif m.role == "user":
                api_msgs.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                blocks: list[dict] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls or []:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                api_msgs.append({"role": "assistant", "content": blocks})
            elif m.role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content,
                }
                # 连续的 tool 结果合并进同一条 user 消息（Anthropic 要求）。
                if api_msgs and api_msgs[-1]["role"] == "user" \
                        and isinstance(api_msgs[-1]["content"], list):
                    api_msgs[-1]["content"].append(block)
                else:
                    api_msgs.append({"role": "user", "content": [block]})
        return "\n\n".join(system_parts), api_msgs

    def stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
    ) -> Iterator[TextDelta | TurnComplete]:
        system, api_msgs = self._split(messages)
        payload = {
            "model": self.model,
            "max_tokens": _DEFAULT_MAX_TOKENS,
            "temperature": self.temperature,
            "messages": api_msgs,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

        text_parts: list[str] = []
        # content block 按 index 累积；tool_use 的 input 以 partial_json 分片回。
        blocks: dict[int, dict] = {}
        stop_reason = "end_turn"

        try:
            resp = requests.post(
                self._url(), headers=headers, json=payload,
                stream=True, timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise LLMError(f"Anthropic 请求失败：{exc}") from exc

        with resp:
            if resp.status_code != 200:
                raise LLMError(f"Anthropic 返回 {resp.status_code}：{resp.text[:500]}")
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[len("data:"):].strip()
                try:
                    evt = json.loads(data)
                except json.JSONDecodeError:
                    continue
                etype = evt.get("type")

                if etype == "content_block_start":
                    idx = evt.get("index", 0)
                    cb = evt.get("content_block") or {}
                    blocks[idx] = {
                        "type": cb.get("type"),
                        "id": cb.get("id", ""),
                        "name": cb.get("name", ""),
                        "json": "",
                    }
                elif etype == "content_block_delta":
                    idx = evt.get("index", 0)
                    d = evt.get("delta") or {}
                    if d.get("type") == "text_delta":
                        txt = d.get("text", "")
                        if txt:
                            text_parts.append(txt)
                            yield TextDelta(txt)
                    elif d.get("type") == "input_json_delta":
                        blocks.setdefault(idx, {"type": "tool_use", "id": "", "name": "", "json": ""})
                        blocks[idx]["json"] += d.get("partial_json", "")
                elif etype == "message_delta":
                    sr = (evt.get("delta") or {}).get("stop_reason")
                    if sr:
                        stop_reason = sr
                elif etype == "message_stop":
                    break

        tool_calls = _assemble_tool_calls(blocks)
        finish_reason = "tool_calls" if (stop_reason == "tool_use" or tool_calls) else "stop"
        yield TurnComplete(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )


def _assemble_tool_calls(blocks: dict[int, dict]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for idx in sorted(blocks):
        b = blocks[idx]
        if b.get("type") != "tool_use" or not b.get("name"):
            continue
        raw = b.get("json") or "{}"
        try:
            args = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            logger.warning("Anthropic 工具参数解析失败，按空参处理：%s", raw[:200])
            args = {}
        calls.append(ToolCall(id=b.get("id") or f"toolu_{idx}", name=b["name"], arguments=args))
    return calls
