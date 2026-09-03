"""Markdown rendering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import make_item
from trend.config import DEFAULT_CATEGORIES
from trend.models import Cluster, Digest, Entry
from trend.render import render_markdown, render_summary_text, source_label

CATEGORIES = list(DEFAULT_CATEGORIES)
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _digest(entries: list[Entry], *, provider: str = "gemini", intro: str = "") -> Digest:
    return Digest(
        week="2026-W36",
        generated_at=NOW,
        window_start=NOW - timedelta(days=7),
        window_end=NOW,
        entries=entries,
        intro=intro,
        provider=provider,
        stats={"fetched": 220, "clustered": 140},
    )


def _entry(**kwargs) -> Entry:
    defaults = {
        "cluster": Cluster(
            items=[
                make_item(
                    "PostgreSQL 18 released",
                    "https://postgresql.org/news/18",
                    points=920,
                    comments=310,
                )
            ]
        ),
        "category": "Infrastructure & Cloud",
        "headline": "PostgreSQL 18 ships async I/O",
        "summary": "The release adds asynchronous I/O for sequential scans.",
        "why_it_matters": "Read-heavy workloads may see throughput gains without tuning.",
    }
    return Entry(**{**defaults, **kwargs})


def test_renders_header_and_window() -> None:
    md = render_markdown(_digest([_entry()]), CATEGORIES)
    assert "# Tech Trends — 2026-W36" in md
    assert "2026-08-27 to 2026-09-03" in md
    assert "1 stories" in md


def test_renders_entry_with_link_and_metadata() -> None:
    md = render_markdown(_digest([_entry()]), CATEGORIES)
    assert "[PostgreSQL 18 ships async I/O](https://postgresql.org/news/18)" in md
    assert "Hacker News" in md
    assert "920 points" in md
    assert "310 comments" in md
    assert "**Why it matters:**" in md


def test_entries_are_grouped_in_configured_category_order() -> None:
    entries = [
        _entry(category="Security", headline="Sec story"),
        _entry(category="AI & Machine Learning", headline="AI story"),
    ]
    md = render_markdown(_digest(entries), CATEGORIES)
    # AI precedes Security in DEFAULT_CATEGORIES regardless of entry order.
    assert md.index("## AI & Machine Learning") < md.index("## Security")


def test_unknown_category_is_still_rendered() -> None:
    md = render_markdown(_digest([_entry(category="Wildcard")]), CATEGORIES)
    assert "## Wildcard" in md


def test_contents_table_appears_for_multiple_categories() -> None:
    entries = [_entry(category="Security"), _entry(category="Developer Tools")]
    md = render_markdown(_digest(entries), CATEGORIES)
    assert "## Contents" in md
    assert "[Security](#security)" in md
    assert "[Developer Tools](#developer-tools)" in md


def test_contents_table_omitted_for_single_category() -> None:
    md = render_markdown(_digest([_entry()]), CATEGORIES)
    assert "## Contents" not in md


def test_github_entry_shows_stars_and_language() -> None:
    entry = _entry(
        cluster=Cluster(
            items=[
                make_item(
                    "owner/tool: a fast thing",
                    "https://github.com/owner/tool",
                    source="github",
                    points=4200,
                    extra={"stars": 4200, "language": "Rust"},
                )
            ]
        )
    )
    md = render_markdown(_digest([entry]), CATEGORIES)
    assert "4,200 stars" in md
    assert "Rust" in md
    assert "GitHub" in md


def test_discussion_link_rendered_when_distinct() -> None:
    item = make_item("A story", "https://blog.example/post", points=300)
    item.discussion_url = "https://news.ycombinator.com/item?id=1"
    md = render_markdown(_digest([_entry(cluster=Cluster(items=[item]))]), CATEGORIES)
    assert "[Discussion](https://news.ycombinator.com/item?id=1)" in md


def test_corroborating_source_links_are_listed() -> None:
    cluster = Cluster(
        items=[
            make_item("Rust 1.90", "https://rust.example/a", points=500),
            make_item("Rust 1.90", "https://other.example/b", source="rss:Rust Blog"),
        ]
    )
    md = render_markdown(_digest([_entry(cluster=cluster)]), CATEGORIES)
    assert "[Rust Blog](https://other.example/b)" in md


def test_footer_reports_provider_and_stats() -> None:
    md = render_markdown(_digest([_entry()]), CATEGORIES)
    assert "summarized by gemini" in md
    assert "220 items fetched" in md
    assert "140 unique stories" in md


def test_footer_states_degraded_mode_explicitly() -> None:
    """A digest built without an LLM must say so rather than look normal."""
    md = render_markdown(_digest([_entry()], provider=""), CATEGORIES)
    assert "no LLM available" in md
    assert "summarized by" not in md


def test_intro_is_included_when_present() -> None:
    md = render_markdown(_digest([_entry()], intro="Databases dominated the week."), CATEGORIES)
    assert "Databases dominated the week." in md


def test_missing_optional_fields_render_cleanly() -> None:
    entry = _entry(summary="", why_it_matters="")
    md = render_markdown(_digest([entry]), CATEGORIES)
    assert "**Why it matters:**" not in md
    assert "PostgreSQL 18 ships async I/O" in md


def test_empty_digest_renders_without_error() -> None:
    md = render_markdown(_digest([]), CATEGORIES)
    assert "# Tech Trends — 2026-W36" in md
    assert "0 stories" in md


def test_headline_falls_back_to_source_title() -> None:
    md = render_markdown(_digest([_entry(headline="")]), CATEGORIES)
    assert "PostgreSQL 18 released" in md


def test_source_label_mapping() -> None:
    assert source_label("hackernews") == "Hacker News"
    assert source_label("github") == "GitHub"
    assert source_label("arxiv") == "arXiv"
    assert source_label("rss:Cloudflare Blog") == "Cloudflare Blog"
    assert source_label("mystery") == "mystery"


def test_summary_text_for_webhooks() -> None:
    entries = [_entry(headline=f"Story {i}") for i in range(12)]
    text = render_summary_text(_digest(entries), limit=5)
    assert "Tech Trends 2026-W36" in text
    assert "1. Story 0" in text
    assert "5. Story 4" in text
    assert "Story 5" not in text
    assert "and 7 more" in text
