"""Summarization, including the no-LLM degraded path."""

from __future__ import annotations

import json

from conftest import FakeProvider, make_item
from trend.config import DEFAULT_CATEGORIES
from trend.llm.base import LLMError, RateLimited
from trend.llm.router import Router
from trend.models import Cluster
from trend.summarize import summarize, write_intro

CATEGORIES = list(DEFAULT_CATEGORIES)


def _clusters(n: int = 3) -> list[Cluster]:
    return [
        Cluster(
            items=[
                make_item(
                    f"Story {i} about kubernetes scaling",
                    f"https://example.com/{i}",
                    points=100 * (n - i),
                    excerpt=f"Details for story {i}.",
                )
            ]
        )
        for i in range(n)
    ]


def _model_reply(count: int, offset: int = 0, category: str = "Developer Tools") -> str:
    return json.dumps(
        {
            "items": [
                {
                    "index": offset + i,
                    "category": category,
                    "headline": f"Headline {offset + i}",
                    "summary": f"Summary {offset + i}.",
                    "why_it_matters": f"Matters {offset + i}.",
                }
                for i in range(count)
            ]
        }
    )


def test_model_output_is_mapped_onto_clusters() -> None:
    clusters = _clusters(3)
    router = Router([FakeProvider("a", [_model_reply(3)])])
    entries, provider = summarize(clusters, CATEGORIES, router, batch_size=12)

    assert provider == "a"
    assert [e.headline for e in entries] == ["Headline 0", "Headline 1", "Headline 2"]
    assert entries[0].summary == "Summary 0."
    assert entries[0].why_it_matters == "Matters 0."
    assert entries[0].category == "Developer Tools"
    # Entries stay aligned with their source clusters.
    assert entries[0].cluster is clusters[0]


def test_batching_splits_requests() -> None:
    """Batching is what keeps a run inside free-tier request-per-day limits."""
    clusters = _clusters(5)
    provider = FakeProvider("a", [_model_reply(2, 0), _model_reply(2, 2), _model_reply(1, 4)])
    entries, _ = summarize(clusters, CATEGORIES, Router([provider]), batch_size=2)

    assert provider.calls == 3
    assert len(entries) == 5
    assert all(e.headline.startswith("Headline") for e in entries)


def test_no_provider_configured_uses_heuristic_fallback() -> None:
    """A digest must still be produced with zero LLM access."""
    clusters = _clusters(2)
    entries, provider = summarize(clusters, CATEGORIES, Router([]))

    assert provider == ""
    assert len(entries) == 2
    # Original titles and upstream excerpts, never model text.
    assert entries[0].headline == clusters[0].primary.title
    assert entries[0].summary == "Details for story 0."
    assert entries[0].why_it_matters == ""


def test_all_providers_failing_falls_back_per_item() -> None:
    clusters = _clusters(2)
    router = Router([FakeProvider("a", [RateLimited("quota")])])
    entries, provider = summarize(clusters, CATEGORIES, router)

    assert provider == ""
    assert len(entries) == 2
    assert entries[0].headline == clusters[0].primary.title


def test_partial_model_response_fills_gaps_with_fallback() -> None:
    """A model that returns 2 of 3 items must not shrink the digest."""
    clusters = _clusters(3)
    router = Router([FakeProvider("a", [_model_reply(2)])])
    entries, _ = summarize(clusters, CATEGORIES, router, batch_size=12)

    assert len(entries) == 3
    assert entries[0].headline == "Headline 0"
    assert entries[2].headline == clusters[2].primary.title


def test_invented_category_is_replaced() -> None:
    """Unknown categories would break section grouping, so they are reassigned."""
    clusters = _clusters(1)
    reply = json.dumps(
        {
            "items": [
                {
                    "index": 0,
                    "category": "Totally Made Up Category",
                    "headline": "H",
                    "summary": "S",
                    "why_it_matters": "W",
                }
            ]
        }
    )
    entries, _ = summarize(clusters, CATEGORIES, Router([FakeProvider("a", [reply])]))
    assert entries[0].category in CATEGORIES
    # Keyword routing picks Infrastructure for a kubernetes headline.
    assert entries[0].category == "Infrastructure & Cloud"


def test_out_of_range_index_is_ignored() -> None:
    clusters = _clusters(1)
    reply = json.dumps({"items": [{"index": 99, "category": "Security", "headline": "X"}]})
    entries, _ = summarize(clusters, CATEGORIES, Router([FakeProvider("a", [reply])]))
    assert len(entries) == 1
    assert entries[0].headline == clusters[0].primary.title


def test_bare_array_response_is_accepted() -> None:
    """Models often drop the wrapper object despite the prompt."""
    clusters = _clusters(1)
    reply = json.dumps([{"index": 0, "category": "Security", "headline": "H", "summary": "S"}])
    entries, _ = summarize(clusters, CATEGORIES, Router([FakeProvider("a", [reply])]))
    assert entries[0].headline == "H"
    assert entries[0].category == "Security"


def test_alternate_wrapper_keys_are_accepted() -> None:
    clusters = _clusters(1)
    reply = json.dumps({"results": [{"index": 0, "category": "Security", "headline": "H"}]})
    entries, _ = summarize(clusters, CATEGORIES, Router([FakeProvider("a", [reply])]))
    assert entries[0].headline == "H"


def test_empty_headline_falls_back_to_title() -> None:
    clusters = _clusters(1)
    reply = json.dumps({"items": [{"index": 0, "category": "Security", "headline": ""}]})
    entries, _ = summarize(clusters, CATEGORIES, Router([FakeProvider("a", [reply])]))
    assert entries[0].headline == clusters[0].primary.title


def test_empty_cluster_list_is_safe() -> None:
    assert summarize([], CATEGORIES, Router([FakeProvider("a", [])])) == ([], "")


def test_arxiv_items_route_to_research_in_fallback() -> None:
    cluster = Cluster(
        items=[
            make_item(
                "On the convergence of gradient methods",
                "https://arxiv.org/abs/1",
                source="arxiv",
                excerpt="We present a proof.",
            )
        ]
    )
    entries, _ = summarize([cluster], CATEGORIES, Router([]))
    assert entries[0].category == "Research"


def test_security_keywords_route_to_security_in_fallback() -> None:
    cluster = Cluster(
        items=[make_item("Critical CVE in OpenSSL", "https://a.example/1", points=500)]
    )
    entries, _ = summarize([cluster], CATEGORIES, Router([]))
    assert entries[0].category == "Security"


def test_long_excerpt_is_truncated_in_fallback() -> None:
    cluster = Cluster(items=[make_item("A story", "https://a.example/1", excerpt="word " * 200)])
    entries, _ = summarize([cluster], CATEGORIES, Router([]))
    assert len(entries[0].summary) <= 280
    assert entries[0].summary.endswith("...")


# --- intro -----------------------------------------------------------------


def test_write_intro_returns_text() -> None:
    clusters = _clusters(1)
    entries, _ = summarize(clusters, CATEGORIES, Router([FakeProvider("a", [_model_reply(1)])]))
    intro = write_intro(entries, Router([FakeProvider("b", ["A quiet week overall."])]))
    assert intro == "A quiet week overall."


def test_write_intro_is_optional_on_failure() -> None:
    clusters = _clusters(1)
    entries, _ = summarize(clusters, CATEGORIES, Router([]))
    assert write_intro(entries, Router([FakeProvider("b", [LLMError("down")])])) == ""


def test_write_intro_empty_without_entries() -> None:
    assert write_intro([], Router([FakeProvider("a", ["x"])])) == ""
