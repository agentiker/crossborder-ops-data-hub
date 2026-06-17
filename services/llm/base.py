"""LLM Provider 基类（plan/15 Phase A）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from services.llm.types import ChatMessage, TextDelta, ToolSpec, TurnComplete


class LLMProvider(ABC):
    """统一的流式 + 工具调用接口。具体协议由子类实现。"""

    def __init__(self, *, base_url: str, api_key: str, model: str,
                 temperature: float = 0.3, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    @abstractmethod
    def stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
    ) -> Iterator[TextDelta | TurnComplete]:
        """对一段对话生成一轮回复。

        先 yield 零个或多个 TextDelta（文本增量），最后 yield 恰好一个 TurnComplete
        （含本轮完整文本 + 模型要调的工具 + finish_reason）。
        """
        raise NotImplementedError
