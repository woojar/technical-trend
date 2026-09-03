"""Provider chain failover and lenient JSON parsing."""

from __future__ import annotations

import pytest

from conftest import FakeProvider
from trend.llm.base import (
    BadResponse,
    LLMError,
    NotConfigured,
    RateLimited,
    Unavailable,
)
from trend.llm.router import Router, build_provider, parse_json_loose


def test_first_configured_provider_wins() -> None:
    a = FakeProvider("a", ["from a"])
    b = FakeProvider("b", ["from b"])
    router = Router([a, b])
    assert router.complete("s", "u").text == "from a"
    assert b.calls == 0
    assert router.last_used == "a"


def test_unconfigured_providers_are_skipped() -> None:
    a = FakeProvider("a", ["never"], configured=False)
    b = FakeProvider("b", ["from b"])
    router = Router([a, b])
    assert router.complete("s", "u").provider == "b"
    assert a.calls == 0


def test_rate_limit_fails_over_without_retrying() -> None:
    """A free-tier 429 means hours of waiting; retrying the same provider is waste."""
    a = FakeProvider("a", [RateLimited("quota gone"), "would work later"])
    b = FakeProvider("b", ["from b"])
    router = Router([a, b])
    assert router.complete("s", "u").provider == "b"
    assert a.calls == 1


def test_transient_error_is_retried_once_on_same_provider(monkeypatch) -> None:
    monkeypatch.setattr("trend.llm.router.time.sleep", lambda _: None)
    a = FakeProvider("a", [Unavailable("503"), "recovered"])
    b = FakeProvider("b", ["from b"])
    router = Router([a, b])
    result = router.complete("s", "u")
    assert result.provider == "a"
    assert result.text == "recovered"
    assert a.calls == 2
    assert b.calls == 0


def test_transient_error_fails_over_after_one_retry(monkeypatch) -> None:
    monkeypatch.setattr("trend.llm.router.time.sleep", lambda _: None)
    a = FakeProvider("a", [Unavailable("503"), Unavailable("503 again")])
    b = FakeProvider("b", ["from b"])
    router = Router([a, b])
    assert router.complete("s", "u").provider == "b"
    assert a.calls == 2


def test_bad_response_fails_over() -> None:
    a = FakeProvider("a", [BadResponse("no json support")])
    b = FakeProvider("b", ["from b"])
    assert Router([a, b]).complete("s", "u").provider == "b"


def test_not_configured_at_call_time_fails_over() -> None:
    a = FakeProvider("a", [NotConfigured("key vanished")])
    b = FakeProvider("b", ["from b"])
    assert Router([a, b]).complete("s", "u").provider == "b"


def test_all_providers_failing_raises_with_context() -> None:
    a = FakeProvider("a", [RateLimited("a limited")])
    b = FakeProvider("b", [RateLimited("b limited")])
    with pytest.raises(LLMError) as exc:
        Router([a, b]).complete("s", "u")
    assert "a limited" in str(exc.value)
    assert "b limited" in str(exc.value)


def test_has_available_reflects_configuration() -> None:
    assert not Router([FakeProvider("a", [], configured=False)]).has_available
    assert Router([FakeProvider("a", [])]).has_available
    assert not Router([]).has_available


def test_describe_reports_each_provider() -> None:
    router = Router([FakeProvider("a", []), FakeProvider("b", [], configured=False)])
    assert router.describe() == [("a", "a-model", True), ("b", "b-model", False)]


def test_complete_json_walks_the_chain() -> None:
    a = FakeProvider("a", [BadResponse("nope")])
    b = FakeProvider("b", ['{"items": [{"index": 0}]}'])
    assert Router([a, b]).complete_json("s", "u") == {"items": [{"index": 0}]}


# --- parse_json_loose ------------------------------------------------------


def test_parse_plain_json() -> None:
    assert parse_json_loose('{"a": 1}') == {"a": 1}


def test_parse_json_in_code_fence() -> None:
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_in_bare_fence() -> None:
    assert parse_json_loose('```\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_with_leading_prose() -> None:
    """Small free models routinely ignore "JSON only"."""
    assert parse_json_loose('Sure! Here is the result:\n{"a": 1}') == {"a": 1}


def test_parse_json_array_with_prose() -> None:
    assert parse_json_loose('Here you go: [{"a": 1}] Hope that helps!') == [{"a": 1}]


def test_parse_nested_json_keeps_outermost_object() -> None:
    text = 'noise {"items": [{"index": 0, "nested": {"k": "v"}}]} trailing'
    assert parse_json_loose(text) == {"items": [{"index": 0, "nested": {"k": "v"}}]}


def test_unparseable_response_raises() -> None:
    with pytest.raises(BadResponse):
        parse_json_loose("I cannot help with that request.")


# --- build_provider --------------------------------------------------------


def test_build_gemini_provider() -> None:
    provider = build_provider(
        {"provider": "gemini", "model": "gemini-flash-latest", "api_key_env": "X_KEY"}
    )
    assert provider is not None
    assert provider.name == "gemini"
    assert provider.model == "gemini-flash-latest"


def test_build_openai_compat_provider() -> None:
    provider = build_provider(
        {
            "provider": "openai_compat",
            "name": "groq",
            "base_url": "https://api.groq.com/openai/v1",
            "model": "llama-3.3-70b-versatile",
            "api_key_env": "GROQ_API_KEY",
        }
    )
    assert provider is not None
    assert provider.name == "groq"


def test_keyless_provider_is_available_without_env() -> None:
    """Local Ollama needs no key, so an empty api_key_env means always available."""
    provider = build_provider(
        {
            "provider": "openai_compat",
            "name": "ollama",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5:7b-instruct",
            "api_key_env": "",
        }
    )
    assert provider is not None
    assert provider.available() is True


def test_keyed_provider_unavailable_without_env(monkeypatch) -> None:
    monkeypatch.delenv("MISSING_KEY", raising=False)
    provider = build_provider({"provider": "gemini", "model": "m", "api_key_env": "MISSING_KEY"})
    assert provider is not None
    assert provider.available() is False


@pytest.mark.parametrize(
    "spec",
    [
        {"provider": "unknown_kind", "model": "m"},
        {"provider": "openai_compat", "model": "m"},  # no base_url
        {"provider": "gemini"},  # no model
        {"model": "m"},  # no provider
    ],
)
def test_invalid_specs_are_skipped(spec: dict) -> None:
    assert build_provider(spec) is None


def test_from_config_drops_invalid_entries() -> None:
    router = Router.from_config(
        [
            {"provider": "gemini", "model": "gemini-flash-latest", "api_key_env": "K"},
            {"provider": "bogus", "model": "m"},
        ]
    )
    assert len(router.providers) == 1
