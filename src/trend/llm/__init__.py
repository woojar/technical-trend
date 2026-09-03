"""Free-LLM provider layer with failover."""

from __future__ import annotations

from trend.llm.base import (
    BadResponse,
    Completion,
    LLMError,
    NotConfigured,
    Provider,
    RateLimited,
    Unavailable,
)
from trend.llm.gemini import GeminiProvider
from trend.llm.openai_compat import OpenAICompatProvider
from trend.llm.router import Router, build_provider, parse_json_loose

__all__ = [
    "BadResponse",
    "Completion",
    "GeminiProvider",
    "LLMError",
    "NotConfigured",
    "OpenAICompatProvider",
    "Provider",
    "RateLimited",
    "Router",
    "Unavailable",
    "build_provider",
    "parse_json_loose",
]
