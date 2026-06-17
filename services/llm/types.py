"""自建 Web agent 的 LLM Provider 抽象类型（plan/15 Phase A）。

统一的对话/工具调用数据结构，与具体 provider 无关。适配器（openai_compat /
anthropic）负责把这些类型翻译成各家 API 的请求体、并把各家流式响应翻译回
统一的流式事件（TextDelta / TurnComplete）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolSpec:
    """一个可供模型调用的工具声明（JSON Schema 参数）。"""

    name: str
    description: str
    parameters: dict  # JSON Schema object（type=object, properties=...）


@dataclass
class ToolCall:
    """模型发起的一次工具调用。"""

    id: str
    name: str
    arguments: dict


@dataclass
class ChatMessage:
    """对话历史中的一条消息（统一格式，仿 OpenAI 角色模型）。

    - role=system/user：仅 content。
    - role=assistant：content（可空）+ 可选 tool_calls（模型本轮要调的工具）。
    - role=tool：一条工具执行结果，需带 tool_call_id + name。
    """

    role: str  # system / user / assistant / tool
    content: str = ""
    tool_calls: Optional[list[ToolCall]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


# ── 流式事件：provider.stream() 先 yield 若干 TextDelta，最后 yield 一个 TurnComplete ──


@dataclass
class TextDelta:
    """流式文本增量。"""

    text: str


@dataclass
class TurnComplete:
    """本轮（一次模型生成）结束。

    finish_reason="tool_calls" 表示模型要调工具（tool_calls 非空）；
    "stop" 表示正常收尾。agent loop 据此决定是否继续。
    """

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"


class LLMError(RuntimeError):
    """LLM 请求失败（网络/鉴权/非 200/响应不可解析）。"""
