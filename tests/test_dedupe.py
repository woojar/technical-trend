"""URL canonicalization and clustering."""

from __future__ import annotations

import pytest

from conftest import make_item
from trend.dedupe import canonicalize_url, cluster_items, title_similarity, title_tokens


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Tracking parameters carry no identity.
        (
            "https://example.com/post?utm_source=hn&utm_medium=social",
            "https://example.com/post",
        ),
        ("https://example.com/post?ref=hackernews", "https://example.com/post"),
        ("https://example.com/post?fbclid=abc123", "https://example.com/post"),
        # Real parameters survive, sorted for stability.
        ("https://example.com/p?b=2&a=1", "https://example.com/p?a=1&b=2"),
        # Mirror hosts, scheme, fragments, trailing slashes.
        ("http://www.example.com/post/", "https://example.com/post"),
        ("https://m.example.com/post", "https://example.com/post"),
        ("https://example.com/post#section-2", "https://example.com/post"),
        ("https://example.com/a//b/", "https://example.com/a/b"),
        ("https://example.com/docs/index.html", "https://example.com/docs"),
        # Case in host only, never in path.
        ("https://EXAMPLE.com/Post", "https://example.com/Post"),
        # Schemeless input still normalizes.
        ("example.com/post", "https://example.com/post"),
        ("", ""),
    ],
)
def test_canonicalize_url(raw: str, expected: str) -> None:
    assert canonicalize_url(raw) == expected


def test_canonicalize_keeps_short_hosts_intact() -> None:
    """The mirror-prefix strip must not eat a legitimate short host."""
    assert canonicalize_url("https://m.dev/x") == "https://m.dev/x"


def test_title_tokens_drops_stopwords_and_boilerplate() -> None:
    tokens = title_tokens("Show HN: A New Way to Use Rust for Web Servers")
    assert "rust" in tokens
    assert "web" in tokens
    assert "servers" in tokens
    # Announcement boilerplate and stopwords carry no signal.
    for noise in ("show", "hn", "a", "new", "way", "to", "use", "for"):
        assert noise not in tokens


def test_title_similarity_bounds() -> None:
    assert title_similarity("Rust 1.90 released", "Rust 1.90 released") == 1.0
    assert title_similarity("Rust 1.90 released", "") == 0.0
    assert title_similarity("PostgreSQL 18 performance", "Kubernetes networking") == 0.0


def test_cluster_merges_same_url_across_sources() -> None:
    items = [
        make_item("Rust 1.90 released", "https://blog.rust-lang.org/rust-1.90", points=400),
        make_item(
            "Rust 1.90 released",
            "https://blog.rust-lang.org/rust-1.90?utm_source=x",
            source="rss:Rust Blog",
        ),
    ]
    clusters = cluster_items(items)
    assert len(clusters) == 1
    assert sorted(clusters[0].sources) == ["hackernews", "rss:Rust Blog"]


def test_cluster_merges_similar_titles_on_different_urls() -> None:
    """The same announcement covered by two outlets at different URLs."""
    items = [
        make_item(
            "PostgreSQL 18 released with async I/O",
            "https://postgresql.org/about/news/pg18",
            points=800,
        ),
        make_item(
            "PostgreSQL 18 released with async I/O support",
            "https://news.example.com/pg-18-async-io",
            source="rss:News",
        ),
    ]
    clusters = cluster_items(items)
    assert len(clusters) == 1
    assert len(clusters[0].items) == 2


def test_cluster_keeps_unrelated_stories_apart() -> None:
    items = [
        make_item("PostgreSQL 18 released", "https://a.example/pg", points=100),
        make_item("Kubernetes 1.35 networking changes", "https://b.example/k8s", points=90),
        make_item("Rust async runtime comparison", "https://c.example/rust", points=80),
    ]
    assert len(cluster_items(items)) == 3


def test_cluster_primary_is_highest_signal_item() -> None:
    items = [
        make_item("Deno 3 released", "https://x.example/deno", source="rss:Feed", points=5),
        make_item("Deno 3 released", "https://x.example/deno", points=900, comments=200),
    ]
    clusters = cluster_items(items)
    assert len(clusters) == 1
    primary = clusters[0].primary
    assert primary.points == 900
    assert clusters[0].total_points == 905


def test_items_without_url_are_not_merged_together() -> None:
    """Empty URLs must not collapse into a single bucket."""
    items = [
        make_item("First unrelated thing", ""),
        make_item("Second different matter", ""),
    ]
    assert len(cluster_items(items)) == 2


def test_threshold_controls_merge_aggressiveness() -> None:
    items = [
        make_item("Redis forks after license change", "https://a.example/1", points=500),
        make_item("Valkey gains traction after Redis license change", "https://b.example/2"),
    ]
    assert len(cluster_items(items, threshold=0.95)) == 2
    assert len(cluster_items(items, threshold=0.3)) == 1
