"""Shared HTTP session configuration."""

from __future__ import annotations

from trend.http import USER_AGENT, build_llm_session, build_session


def test_source_session_retries_transient_failures() -> None:
    adapter = build_session().get_adapter("https://example.com")
    retry = adapter.max_retries
    assert retry.total == 3
    assert 429 in retry.status_forcelist
    # POST is not idempotent, so status retries must not apply to it.
    assert "POST" not in retry.allowed_methods


def test_llm_session_disables_transport_retries() -> None:
    """Router owns retry policy; stacking urllib3 retries under it stalls runs.

    A configured but unreachable provider (local Ollama that is not running)
    would otherwise cost ~8 backed-off connection attempts per request.
    """
    adapter = build_llm_session().get_adapter("https://example.com")
    assert adapter.max_retries.total == 0
    assert adapter.max_retries.connect == 0


def test_sessions_send_a_user_agent() -> None:
    for session in (build_session(), build_llm_session()):
        assert session.headers["User-Agent"] == USER_AGENT


def test_retry_count_is_configurable() -> None:
    assert build_session(retries=1).get_adapter("https://x").max_retries.total == 1
