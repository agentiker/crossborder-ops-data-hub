"""OpenAI 兼容族适配器（plan/15 Phase A）。

覆盖国外 OpenAI 与国内 DeepSeek / 通义千问(Qwen) / 智谱 GLM / Kimi(Moonshot) /
豆包(火山方舟) / 百度千帆 / 硅基流动等——它们都暴露 /chat/completions 兼容端点，
切换只换 base_url + api_key + model。用 requests 直接打 SSE 流式，零 SDK 依赖。
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


class OpenAICompatProvider(LLMProvider):
    """调用 OpenAI 兼容的 /chat/completions（stream=True）。"""

    def _url(self) -> str:
        # base_url 既兼容带 /v1（如 https://api.deepseek.com/v1）也兼容不带。
        base = self.base_url
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _to_api_messages(self, messages: list[ChatMessage]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m.role == "assistant" and m.tool_calls:
                out.append({
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in m.tool_calls
                    ],
                })
            elif m.role == "tool":
                out.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "content": m.content,
                })
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    def stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
    ) -> Iterator[TextDelta | TurnComplete]:
        payload = {
            "model": self.model,
            "messages": self._to_api_messages(messages),
            "temperature": self.temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        text_parts: list[str] = []
        # tool_calls 按 index 累积（OpenAI 流式分片回 name/arguments）
        tc_acc: dict[int, dict] = {}
        finish_reason = "stop"

        try:
            resp = requests.post(
                self._url(), headers=headers, json=payload,
                stream=True, timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise LLMError(f"OpenAI 兼容请求失败：{exc}") from exc

        with resp:
            if resp.status_code != 200:
                body = resp.text[:500]
                raise LLMError(f"OpenAI 兼容返回 {resp.status_code}：{body}")
            # requests 对 text/event-stream（text/* 且无 charset）默认按 ISO-8859-1 解码，
            # 会把 UTF-8 中文打成乱码；强制 UTF-8 后再 iter_lines(decode_unicode=True)。
            resp.encoding = "utf-8"
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}

                content = delta.get("content")
                if content:
                    text_parts.append(content)
                    yield TextDelta(content)

                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = tc_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]

                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

        tool_calls = _assemble_tool_calls(tc_acc)
        if tool_calls:
            finish_reason = "tool_calls"
        yield TurnComplete(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )


def _assemble_tool_calls(tc_acc: dict[int, dict]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for idx in sorted(tc_acc):
        slot = tc_acc[idx]
        if not slot.get("name"):
            continue
        raw_args = slot.get("args") or "{}"
        try:
            args = json.loads(raw_args) if raw_args.strip() else {}
        except json.JSONDecodeError:
            logger.warning("工具参数 JSON 解析失败，按空参处理：%s", raw_args[:200])
            args = {}
        calls.append(ToolCall(id=slot.get("id") or f"call_{idx}", name=slot["name"], arguments=args))
    return calls
