"""LLM Provider 工厂（plan/15 Phase A）。

按 core.config.settings.llm.provider 选适配器。默认 provider/model 由配置（.env）决定，
代码不写死任何模型 id（国外/国内均可，换 base_url+api_key+model 即切）。
"""

from __future__ import annotations

from core.config import LLMConfig, settings
from services.llm.anthropic import AnthropicProvider
from services.llm.base import LLMProvider
from services.llm.openai_compat import OpenAICompatProvider
from services.llm.types import (
    ChatMessage,
    LLMError,
    TextDelta,
    ToolCall,
    ToolSpec,
    TurnComplete,
)

__all__ = [
    "LLMProvider",
    "ChatMessage",
    "ToolCall",
    "ToolSpec",
    "TextDelta",
    "TurnComplete",
    "LLMError",
    "get_provider",
]


def get_provider(cfg: LLMConfig | None = None) -> LLMProvider:
    """据配置返回 Provider 实例。未配置 api_key/model → LLMError（fail closed）。"""
    cfg = cfg or settings.llm
    if not cfg.api_key or not cfg.model:
        raise LLMError("LLM 未配置：请在 .env 设置 LLM__API_KEY 与 LLM__MODEL")

    common = dict(
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        model=cfg.model,
        temperature=cfg.temperature,
        timeout=cfg.request_timeout_seconds,
    )
    provider = (cfg.provider or "openai").lower()
    if provider == "anthropic":
        return AnthropicProvider(**common)
    if provider in ("openai", "openai_compat", "openai-compatible"):
        return OpenAICompatProvider(**common)
    raise LLMError(f"未知 LLM provider：{cfg.provider}（支持 openai / anthropic）")
