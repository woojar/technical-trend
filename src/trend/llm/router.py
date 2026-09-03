"""Provider chain with automatic failover.

Free tiers fail in ways paid tiers do not: daily quotas run out mid-job, "free"
model slugs get retired, and shared-capacity endpoints return 503 under load.
The router treats every provider as unreliable and walks the chain until one
answers, which is what makes a zero-cost setup viable for an unattended job.

Failure policy:

* ``NotConfigured`` -- skip without noise, the user simply has no key for it.
* ``RateLimited`` -- move on immediately; on a free tier the window is hours.
* ``Unavailable`` -- one retry with backoff, then move on.
* ``BadResponse`` -- move on; usually an unsupported ``response_format``.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

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

log = logging.getLogger(__name__)


def build_provider(
    spec: dict[str, Any], session: requests.Session | None = None
) -> Provider | None:
    """Instantiate one chain entry from its config block."""
    kind = (spec.get("provider") or "").strip()
    model = (spec.get("model") or "").strip()
    if not kind or not model:
        log.warning("llm: chain entry missing provider/model: %r", spec)
        return None

    common = {
        "model": model,
        "api_key_env": spec.get("api_key_env", ""),
        "session": session,
        "temperature": float(spec.get("temperature", 0.3)),
        "max_tokens": int(spec.get("max_tokens", 4096)),
        "timeout": int(spec.get("timeout", 120)),
    }

    if kind == "gemini":
        return GeminiProvider(name=spec.get("name", "gemini"), **common)

    if kind == "openai_compat":
        base_url = (spec.get("base_url") or "").strip()
        if not base_url:
            log.warning("llm: openai_compat entry %r has no base_url", spec.get("name"))
            return None
        return OpenAICompatProvider(
            name=spec.get("name") or base_url,
            base_url=base_url,
            extra_headers=spec.get("headers") or {},
            **common,
        )

    log.warning("llm: unknown provider kind %r", kind)
    return None


class Router:
    """Tries each configured provider in order until one succeeds."""

    def __init__(self, providers: list[Provider]) -> None:
        self.providers = providers
        #: Name of the provider that served the most recent successful call.
        self.last_used: str = ""

    @classmethod
    def from_config(
        cls, chain: list[dict[str, Any]], session: requests.Session | None = None
    ) -> Router:
        providers = [p for p in (build_provider(s, session) for s in chain) if p is not None]
        return cls(providers)

    def describe(self) -> list[tuple[str, str, bool]]:
        """``(name, model, configured)`` for each provider, for the CLI."""
        return [(p.name, p.model, p.available()) for p in self.providers]

    @property
    def has_available(self) -> bool:
        return any(p.available() for p in self.providers)

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> Completion:
        errors: list[str] = []

        for provider in self.providers:
            if not provider.available():
                log.debug("llm: %s not configured, skipping", provider.name)
                continue

            for attempt in (1, 2):
                try:
                    result = provider.complete(system, user, json_mode=json_mode)
                except NotConfigured as exc:
                    errors.append(str(exc))
                    break
                except RateLimited as exc:
                    log.warning("llm: %s rate limited, failing over", provider.name)
                    errors.append(str(exc))
                    break
                except Unavailable as exc:
                    if attempt == 1:
                        log.info("llm: %s unavailable, retrying once: %s", provider.name, exc)
                        time.sleep(2.0)
                        continue
                    errors.append(str(exc))
                    break
                except (BadResponse, LLMError) as exc:
                    errors.append(str(exc))
                    break
                else:
                    if attempt > 1 or provider.name != self.last_used:
                        log.info("llm: using %s (%s)", provider.name, provider.model)
                    self.last_used = provider.name
                    return result

        raise LLMError("all providers failed: " + "; ".join(errors) if errors else "no providers")

    def complete_json(self, system: str, user: str) -> Any:
        """Complete and parse JSON, tolerating the usual model sloppiness."""
        completion = self.complete(system, user, json_mode=True)
        return parse_json_loose(completion.text)


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def parse_json_loose(text: str) -> Any:
    """Parse JSON that a model may have wrapped in prose or code fences.

    Smaller free models routinely ignore "reply with JSON only". Rather than
    discard an otherwise good response, strip fences and fall back to slicing
    the outermost bracket pair.
    """
    candidate = text.strip()

    try:
        return json.loads(candidate)
    except ValueError:
        pass

    fence = _FENCE_RE.search(candidate)
    if fence:
        try:
            return json.loads(fence.group(1))
        except ValueError:
            candidate = fence.group(1)

    for opener, closer in _bracket_order(candidate):
        start, end = candidate.find(opener), candidate.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except ValueError:
                continue

    raise BadResponse(f"could not parse JSON from response: {text[:200]!r}")


def _bracket_order(text: str) -> list[tuple[str, str]]:
    """Try the bracket type that appears first, so arrays are not mistaken for
    their first element.

    Slicing ``{``..``}`` out of ``[{"a": 1}]`` would silently return the inner
    object and drop the rest of the list.
    """
    obj, arr = text.find("{"), text.find("[")
    if arr >= 0 and (obj < 0 or arr < obj):
        return [("[", "]"), ("{", "}")]
    return [("{", "}"), ("[", "]")]
