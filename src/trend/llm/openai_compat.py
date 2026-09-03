"""One client for every OpenAI-compatible endpoint.

Groq, OpenRouter, Cerebras, Together, DeepSeek, GitHub Models and Ollama all
speak ``POST /chat/completions`` with the same JSON shape, so they need one
implementation parameterized by base URL and model rather than five SDKs. Keeping
vendor SDKs out of the dependency tree also keeps the lockfile small and avoids
their conflicting ``httpx`` pins.
"""

from __future__ import annotations

import logging
import os

import requests

from trend.llm.base import (
    BadResponse,
    Completion,
    NotConfigured,
    RateLimited,
    Unavailable,
    Unreachable,
)
from trend.textutil import redact

log = logging.getLogger(__name__)


class OpenAICompatProvider:
    """Chat-completions client for any OpenAI-compatible API."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key_env: str = "",
        session: requests.Session | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: int = 120,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.extra_headers = extra_headers or {}
        self._session = session or requests.Session()

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip() if self.api_key_env else ""

    def available(self) -> bool:
        # An empty api_key_env marks a keyless endpoint such as local Ollama.
        return True if not self.api_key_env else bool(self.api_key)

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> Completion:
        if not self.available():
            raise NotConfigured(f"{self.name}: {self.api_key_env} is not set")

        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.base_url}/chat/completions"
        try:
            resp = self._session.post(url, json=payload, headers=headers, timeout=self.timeout)
        except requests.Timeout as exc:
            # The server is there but slow; a retry may well succeed.
            raise Unavailable(f"{self.name}: timed out after {self.timeout}s") from exc
        except requests.ConnectionError as exc:
            # Nothing is listening. This will not change mid-run.
            raise Unreachable(f"{self.name}: cannot reach {self.base_url}") from exc
        except requests.RequestException as exc:
            raise Unavailable(f"{self.name}: {exc}") from exc

        if resp.status_code == 429:
            raise RateLimited(f"{self.name}: rate limited ({_snippet(resp, self.api_key)})")
        if resp.status_code in (401, 403):
            raise NotConfigured(f"{self.name}: auth rejected ({_snippet(resp, self.api_key)})")
        if resp.status_code >= 500:
            raise Unavailable(
                f"{self.name}: HTTP {resp.status_code} ({_snippet(resp, self.api_key)})"
            )
        if resp.status_code >= 400:
            # 400 on a json_mode request usually means the model does not
            # support response_format; treat as unusable so we fail over.
            raise BadResponse(
                f"{self.name}: HTTP {resp.status_code} ({_snippet(resp, self.api_key)})"
            )

        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise BadResponse(
                f"{self.name}: unexpected payload ({_snippet(resp, self.api_key)})"
            ) from exc

        if not text or not text.strip():
            raise BadResponse(f"{self.name}: empty completion")

        return Completion(text=text.strip(), provider=self.name, model=self.model)


def _snippet(resp: requests.Response, secret: str = "", limit: int = 160) -> str:
    """Compact, credential-free one-line form of an error body, for logging.

    Provider errors arrive as pretty-printed JSON; collapsing the whitespace
    keeps a failover notice on one readable log line. ``secret`` is scrubbed
    because these messages reach logs, and Actions logs are world-readable on a
    public repository.
    """
    try:
        return redact(" ".join(resp.text.split()), secret)[:limit]
    except Exception:  # pragma: no cover - defensive
        return "<unreadable>"
