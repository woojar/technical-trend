"""Shared HTTP session with retries and a polite user agent."""

from __future__ import annotations

import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

USER_AGENT = "technical-trend/0.1 (+https://github.com/woojar/technical-trend)"
DEFAULT_TIMEOUT = 20


def build_session(*, retries: int = 3) -> requests.Session:
    """A session that retries idempotent failures with exponential backoff.

    Public APIs used here (Algolia, GitHub, arXiv) all rate-limit, and a weekly
    unattended job should ride out a transient 429 rather than lose a source.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})

    retry = Retry(
        total=retries,
        connect=retries,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=8)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def build_llm_session() -> requests.Session:
    """Session for LLM provider calls, with transport retries disabled.

    :class:`~trend.llm.router.Router` already implements failover across
    providers plus one retry for transient errors. Stacking urllib3 retries
    underneath turns a single unreachable endpoint (a local Ollama that is not
    running, say) into roughly eight connection attempts with backoff per
    request, which is slow enough to stall a whole run.
    """
    return build_session(retries=0)


def get_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Any:
    """GET and parse JSON, raising for HTTP errors."""
    resp = session.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()
