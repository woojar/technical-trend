"""LLM provider interface and error taxonomy.

The error types exist so the router can distinguish "try the next provider
immediately" from "this one is worth retrying". On free tiers the two cases are
very different: a 429 from Gemini means wait 24 hours, while a 503 usually
clears in seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class LLMError(Exception):
    """Base class for provider failures."""


class RateLimited(LLMError):
    """Quota or requests-per-minute exhausted. Fail over, do not retry."""


class NotConfigured(LLMError):
    """No API key present, so the provider is unusable. Skip silently."""


class Unavailable(LLMError):
    """Transient server-side or network failure. Worth one retry."""


class Unreachable(Unavailable):
    """The endpoint could not be contacted at all.

    Distinguished from :class:`Unavailable` because it is not transient within a
    single run: a refused connection to a local Ollama that is not running will
    be refused again on every later batch. The router drops such a provider for
    the remainder of the run instead of retrying it each time.
    """


class BadResponse(LLMError):
    """The provider replied but the payload was unusable."""


@dataclass(slots=True)
class Completion:
    text: str
    provider: str
    model: str


class Provider(Protocol):
    name: str
    model: str

    def available(self) -> bool:
        """True when configuration (usually an API key) is present."""
        ...

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> Completion:
        """Single-turn completion. Raises a subclass of :class:`LLMError`."""
        ...
