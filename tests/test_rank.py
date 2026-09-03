"""Cluster scoring."""

from __future__ import annotations

from conftest import make_item
from trend.dedupe import cluster_items
from trend.models import Cluster
from trend.rank import score_clusters
from trend.sources import source_weights


def test_cross_source_story_outranks_equal_single_source_story(now) -> None:
    """Corroboration across sources is the strongest quality signal we have."""
    single = Cluster(items=[make_item("Story A", "https://a.example/1", points=500)])
    both = Cluster(
        items=[
            make_item("Story B", "https://b.example/1", points=500),
            make_item("Story B", "https://b.example/1", source="rss:Feed"),
        ]
    )
    score_clusters([single, both], source_weights(), now=now)
    assert both.score > single.score


def test_attention_is_normalized_within_source(now) -> None:
    """GitHub star counts must not simply outrank all Hacker News points."""
    hn_top = Cluster(items=[make_item("HN top", "https://a.example/1", points=600)])
    gh_weak = Cluster(
        items=[
            make_item(
                "owner/repo: minor tool",
                "https://github.com/owner/repo",
                source="github",
                points=200,
            )
        ]
    )
    gh_top = Cluster(
        items=[
            make_item(
                "owner/big: major framework",
                "https://github.com/owner/big",
                source="github",
                points=40000,
            )
        ]
    )
    score_clusters([hn_top, gh_weak, gh_top], source_weights(), now=now)
    # The strongest HN story beats the weakest GitHub repo despite 200 < 600
    # being a much smaller raw gap than 40000 stars would suggest.
    assert hn_top.score > gh_weak.score


def test_recency_breaks_ties(now) -> None:
    fresh = Cluster(items=[make_item("Fresh", "https://a.example/1", points=300, age_days=0.5)])
    stale = Cluster(items=[make_item("Stale", "https://b.example/1", points=300, age_days=6.5)])
    score_clusters([fresh, stale], source_weights(), now=now)
    assert fresh.score > stale.score


def test_comments_count_less_than_points(now) -> None:
    points_heavy = Cluster(items=[make_item("A", "https://a.example/1", points=400)])
    comment_heavy = Cluster(items=[make_item("B", "https://b.example/1", comments=400)])
    score_clusters([points_heavy, comment_heavy], source_weights(), now=now)
    assert points_heavy.score > comment_heavy.score


def test_rss_subsource_inherits_generic_rss_weight(now) -> None:
    """`rss:Some Feed` must resolve to the `rss` weight, not the 0.5 default."""
    feed = Cluster(items=[make_item("X", "https://a.example/1", source="rss:Cloudflare")])
    unknown = Cluster(items=[make_item("Y", "https://b.example/1", source="mystery")])
    score_clusters([feed, unknown], source_weights(), now=now)
    assert feed.score > unknown.score


def test_zero_signal_source_still_scores(now) -> None:
    """arXiv reports no engagement at all; it must not collapse to zero."""
    paper = Cluster(items=[make_item("A paper", "https://arxiv.org/abs/1", source="arxiv")])
    score_clusters([paper], source_weights(), now=now)
    assert paper.score > 0.0


def test_score_clusters_returns_sorted_descending(now) -> None:
    clusters = [
        Cluster(items=[make_item(f"S{i}", f"https://a.example/{i}", points=i * 100)])
        for i in range(1, 6)
    ]
    ranked = score_clusters(clusters, source_weights(), now=now)
    assert [c.score for c in ranked] == sorted((c.score for c in ranked), reverse=True)


def test_empty_input_is_safe(now) -> None:
    assert score_clusters([], source_weights(), now=now) == []


def test_end_to_end_cluster_then_rank(now) -> None:
    items = [
        make_item("Rust 1.90 released", "https://rust.example/1", points=900),
        make_item("Rust 1.90 released", "https://rust.example/1", source="rss:Rust Blog"),
        make_item("Minor CLI tool published", "https://x.example/2", points=20),
        make_item("Paper on program synthesis", "https://arxiv.org/abs/2", source="arxiv"),
    ]
    clusters = score_clusters(cluster_items(items), source_weights(), now=now)
    assert clusters[0].primary.title == "Rust 1.90 released"
    assert len(clusters) == 3
