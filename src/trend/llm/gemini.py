"""Google Gemini adapter.

Gemini is first in the default chain because its free tier is the most generous
of the hosted options and Flash models are more than capable of summarizing and
categorizing news items. It needs its own adapter because the REST shape differs
from OpenAI's: ``contents`` instead of ``messages``, a separate
``systemInstruction``, and ``generationConfig`` for sampling parameters.

Gemini does expose an OpenAI-compatible endpoint, but the native API gives
reliable JSON output via ``responseMimeType``, which matters here.
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
)

log = logging.getLogger(__name__)

DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider:
    def __init__(
        self,
        *,
        model: str = "gemini-2.0-flash",
        api_key_env: str = "GEMINI_API_KEY",
        base_url: str = DEFAULT_BASE,
        session: requests.Session | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: int = 120,
        name: str = "gemini",
    ) -> None:
        self.name = name
        self.model = model
        self.api_key_env = api_key_env
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._session = session or requests.Session()

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()

    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> Completion:
        if not self.available():
            raise NotConfigured(f"{self.name}: {self.api_key_env} is not set")

        generation_config: dict = {
            "temperature": self.temperature,
            "maxOutputTokens": self.max_tokens,
        }
        if json_mode:
            generation_config["responseMimeType"] = "application/json"

        payload = {
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": generation_config,
        }

        url = f"{self.base_url}/models/{self.model}:generateContent"
        try:
            resp = self._session.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    # Header auth avoids leaking the key into request logs.
                    "x-goog-api-key": self.api_key,
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise Unavailable(f"{self.name}: {exc}") from exc

        if resp.status_code == 429:
            raise RateLimited(f"{self.name}: quota exhausted ({_snippet(resp)})")
        if resp.status_code in (401, 403):
            raise NotConfigured(f"{self.name}: auth rejected ({_snippet(resp)})")
        if resp.status_code >= 500:
            raise Unavailable(f"{self.name}: HTTP {resp.status_code} ({_snippet(resp)})")
        if resp.status_code >= 400:
            raise BadResponse(f"{self.name}: HTTP {resp.status_code} ({_snippet(resp)})")

        try:
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                # Safety filters return no candidates at all.
                reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
                raise BadResponse(f"{self.name}: {reason}")
            parts = (candidates[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
        except (ValueError, AttributeError, IndexError, TypeError) as exc:
            raise BadResponse(f"{self.name}: unexpected payload ({_snippet(resp)})") from exc

        if not text.strip():
            raise BadResponse(f"{self.name}: empty completion")

        return Completion(text=text.strip(), provider=self.name, model=self.model)


def _snippet(resp: requests.Response, limit: int = 200) -> str:
    try:
        return resp.text[:limit].replace("\n", " ")
    except Exception:  # pragma: no cover - defensive
        return "<unreadable>"
