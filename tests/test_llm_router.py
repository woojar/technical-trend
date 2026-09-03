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
    Unreachable,
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


def test_transient_error_fails_over_before_retrying(monkeypatch) -> None:
    """A healthy provider beats waiting on an overloaded one.

    Failover is instant while a retry costs a sleep, so the first pass must move
    on rather than retry in place -- the common case on a contended free tier.
    """
    monkeypatch.setattr("trend.llm.router.time.sleep", lambda _: None)
    a = FakeProvider("a", [Unavailable("503"), "recovered"])
    b = FakeProvider("b", ["from b"])
    router = Router([a, b])
    result = router.complete("s", "u")
    assert result.provider == "b"
    assert a.calls == 1  # not retried while b was still untried
    assert b.calls == 1


def test_transient_error_retried_only_after_all_providers_fail(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("trend.llm.router.time.sleep", sleeps.append)
    a = FakeProvider("a", [Unavailable("503"), "recovered"])
    b = FakeProvider("b", [Unavailable("also down"), Unavailable("still down")])
    router = Router([a, b])

    result = router.complete("s", "u")
    assert result.provider == "a"
    assert result.text == "recovered"
    assert a.calls == 2
    # One sleep, paid once for the whole request rather than per provider.
    assert len(sleeps) == 1


def test_rate_limited_provider_is_never_retried(monkeypatch) -> None:
    """A free-tier 429 resets in hours, so a second attempt is pure waste."""
    monkeypatch.setattr("trend.llm.router.time.sleep", lambda _: None)
    a = FakeProvider("a", [RateLimited("quota gone"), "would work"])
    b = FakeProvider("b", [Unavailable("down"), "recovered"])
    router = Router([a, b])

    assert router.complete("s", "u").provider == "b"
    assert a.calls == 1


def test_rate_limited_provider_is_dropped_for_the_rest_of_the_run() -> None:
    """A digest makes several batched calls; re-asking an exhausted free tier on
    each one is several wasted round-trips and a wall of duplicate log lines."""
    a = FakeProvider("a", [RateLimited("daily quota gone"), "would work"])
    b = FakeProvider("b", ["first", "second", "third"])
    router = Router([a, b])

    for _ in range(3):
        assert router.complete("s", "u").provider == "b"

    assert a.calls == 1  # asked once, then skipped
    assert b.calls == 3


def test_exhausting_every_provider_reports_it_clearly() -> None:
    a = FakeProvider("a", [RateLimited("quota gone")])
    router = Router([a])

    # First call surfaces the underlying provider error.
    with pytest.raises(LLMError, match="quota gone"):
        router.complete("s", "u")
    # Later calls must not re-ask, and must say why they gave up.
    with pytest.raises(LLMError, match="every provider was written off"):
        router.complete("s", "u")
    assert a.calls == 1


def test_has_available_reflects_exhausted_quota() -> None:
    """summarize() uses this to choose the heuristic path, so it must go False."""
    router = Router([FakeProvider("a", [RateLimited("quota gone")])])
    assert router.has_available is True
    with pytest.raises(LLMError):
        router.complete("s", "u")
    assert router.has_available is False


def test_bad_response_does_not_exhaust_a_provider() -> None:
    """Only rate limits are sticky; a malformed reply may not recur."""
    a = FakeProvider("a", [BadResponse("bad json"), "recovered"])
    b = FakeProvider("b", ["from b"])
    router = Router([a, b])

    assert router.complete("s", "u").provider == "b"
    assert router.complete("s", "u").provider == "a"
    assert a.calls == 2


def test_unreachable_endpoint_is_dropped_for_the_rest_of_the_run() -> None:
    """A refused socket will be refused again on every later batch.

    Distinguished from a slow server: a local Ollama that is not running should
    cost one failed attempt per run, not one per batch.
    """
    dead = FakeProvider("ollama", [Unreachable("cannot reach localhost:11434")] * 5)
    live = FakeProvider("b", ["first", "second", "third"])
    router = Router([dead, live])

    for _ in range(3):
        assert router.complete("s", "u").provider == "b"

    assert dead.calls == 1


def test_timeout_is_not_sticky(monkeypatch) -> None:
    """A slow server is still there, so it keeps its place in the chain."""
    monkeypatch.setattr("trend.llm.router.time.sleep", lambda _: None)
    slow = FakeProvider("slow", [Unavailable("timed out"), "recovered"])
    other = FakeProvider("other", ["from other", Unavailable("down")])
    router = Router([slow, other])

    assert router.complete("s", "u").provider == "other"
    # slow was not written off, so it is asked again on the next call.
    assert router.complete("s", "u").provider == "slow"
    assert slow.calls == 2


def test_write_off_reasons_are_reported() -> None:
    router = Router(
        [
            FakeProvider("a", [RateLimited("quota gone")]),
            FakeProvider("b", [Unreachable("no socket")]),
        ]
    )
    with pytest.raises(LLMError):
        router.complete("s", "u")
    with pytest.raises(LLMError, match=r"a \(rate limited\), b \(unreachable\)"):
        router.complete("s", "u")


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


def test_no_configured_provider_raises_immediately() -> None:
    router = Router([FakeProvider("a", ["never"], configured=False)])
    with pytest.raises(LLMError, match="no provider is configured"):
        router.complete("s", "u")


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
