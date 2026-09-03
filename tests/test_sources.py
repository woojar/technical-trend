"""Source payload parsing. Exercises the mapping layer only -- no network."""

from __future__ import annotations

from datetime import UTC, datetime

import feedparser

from trend.sources.arxiv import ArxivSource
from trend.sources.github import GitHubSource
from trend.sources.hackernews import HackerNewsSource
from trend.sources.rss import RSSSource

# --- Hacker News -----------------------------------------------------------

HN_HIT = {
    "objectID": "41234567",
    "title": "PostgreSQL 18 released",
    "url": "https://www.postgresql.org/about/news/pg18/",
    "points": 842,
    "num_comments": 301,
    "author": "dbfan",
    "created_at_i": 1756900000,
    "story_text": None,
}


def test_hn_hit_maps_to_item() -> None:
    item = HackerNewsSource()._to_item(HN_HIT)
    assert item is not None
    assert item.title == "PostgreSQL 18 released"
    assert item.url == "https://www.postgresql.org/about/news/pg18/"
    assert item.points == 842
    assert item.comments == 301
    assert item.author == "dbfan"
    assert item.source == "hackernews"
    assert item.discussion_url == "https://news.ycombinator.com/item?id=41234567"
    assert item.published.tzinfo is not None


def test_hn_text_post_links_to_thread() -> None:
    """Ask HN posts have no external URL; the thread must be used instead."""
    item = HackerNewsSource()._to_item({**HN_HIT, "url": None, "title": "Ask HN: tooling?"})
    assert item is not None
    assert item.url == "https://news.ycombinator.com/item?id=41234567"


def test_hn_hit_without_title_is_dropped() -> None:
    assert HackerNewsSource()._to_item({**HN_HIT, "title": ""}) is None


def test_hn_hit_without_id_is_dropped() -> None:
    assert HackerNewsSource()._to_item({**HN_HIT, "objectID": None}) is None


def test_hn_missing_counts_default_to_zero() -> None:
    item = HackerNewsSource()._to_item({**HN_HIT, "points": None, "num_comments": None})
    assert item is not None
    assert item.points == 0
    assert item.comments == 0


# --- GitHub ----------------------------------------------------------------

GH_REPO = {
    "id": 987654,
    "full_name": "acme/turbo-parser",
    "html_url": "https://github.com/acme/turbo-parser",
    "description": "A very fast parser",
    "language": "Rust",
    "stargazers_count": 5321,
    "forks_count": 120,
    "open_issues_count": 14,
    "created_at": "2026-07-15T10:20:30Z",
    "owner": {"login": "acme"},
    "topics": ["parser", "rust"],
}


def test_github_repo_maps_to_item() -> None:
    item = GitHubSource()._to_item(GH_REPO)
    assert item is not None
    # Description is folded into the title so dedupe and the LLM have signal.
    assert item.title == "acme/turbo-parser: A very fast parser"
    assert item.points == 5321
    assert item.extra["language"] == "Rust"
    assert item.extra["stars"] == 5321
    assert item.author == "acme"
    assert item.published == datetime(2026, 7, 15, 10, 20, 30, tzinfo=UTC)


def test_github_repo_without_description() -> None:
    item = GitHubSource()._to_item({**GH_REPO, "description": None})
    assert item is not None
    assert item.title == "acme/turbo-parser"


def test_github_repo_with_bad_date_still_parses() -> None:
    item = GitHubSource()._to_item({**GH_REPO, "created_at": "not-a-date"})
    assert item is not None
    assert item.published.tzinfo is not None


def test_github_repo_without_url_is_dropped() -> None:
    assert GitHubSource()._to_item({**GH_REPO, "html_url": ""}) is None


# --- arXiv -----------------------------------------------------------------

ARXIV_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2609.01234v1</id>
    <published>2026-09-01T08:00:00Z</published>
    <title>Efficient Program
      Synthesis at Scale</title>
    <summary>  We present a method for
      scaling program synthesis.  </summary>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
    <link href="http://arxiv.org/abs/2609.01234v1" rel="alternate"/>
  </entry>
</feed>
"""


def test_arxiv_entry_maps_to_item() -> None:
    entry = feedparser.parse(ARXIV_ATOM).entries[0]
    item = ArxivSource()._to_item(entry, "cs.SE")
    assert item is not None
    # Multi-line titles and abstracts must be collapsed to single lines.
    assert item.title == "Efficient Program Synthesis at Scale"
    assert item.excerpt == "We present a method for scaling program synthesis."
    assert item.author == "Ada Lovelace, Alan Turing"
    assert item.source == "arxiv"
    assert item.points == 0
    assert item.extra["arxiv_category"] == "cs.SE"


def test_arxiv_truncates_author_list() -> None:
    atom = ARXIV_ATOM.replace(
        b"<author><name>Alan Turing</name></author>",
        b"<author><name>B</name></author><author><name>C</name></author>"
        b"<author><name>D</name></author>",
    )
    entry = feedparser.parse(atom).entries[0]
    item = ArxivSource()._to_item(entry, "cs.SE")
    assert item is not None
    assert item.author.endswith("et al.")


# --- RSS -------------------------------------------------------------------

RSS_XML = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Example Blog</title>
  <item>
    <title>Scaling our edge network</title>
    <link>https://blog.example.com/edge-scaling</link>
    <pubDate>Tue, 01 Sep 2026 09:00:00 GMT</pubDate>
    <description>&lt;p&gt;We rebuilt &lt;b&gt;routing&lt;/b&gt;.&lt;/p&gt;</description>
    <author>infra@example.com</author>
  </item>
</channel></rss>
"""


def test_rss_entry_maps_to_item() -> None:
    entry = feedparser.parse(RSS_XML).entries[0]
    item = RSSSource()._to_item(entry, "Example Blog")
    assert item is not None
    assert item.title == "Scaling our edge network"
    assert item.url == "https://blog.example.com/edge-scaling"
    # HTML in feed summaries must be reduced to plain text.
    assert item.excerpt == "We rebuilt routing."
    assert item.source == "rss:Example Blog"
    assert item.extra["feed"] == "Example Blog"


def test_rss_entry_without_date_is_dropped() -> None:
    """Undated entries cannot be windowed, so they must not leak in."""
    xml = RSS_XML.replace(b"<pubDate>Tue, 01 Sep 2026 09:00:00 GMT</pubDate>", b"")
    entry = feedparser.parse(xml).entries[0]
    assert RSSSource()._to_item(entry, "Example Blog") is None


def test_rss_entry_without_link_is_dropped() -> None:
    xml = RSS_XML.replace(b"<link>https://blog.example.com/edge-scaling</link>", b"")
    entry = feedparser.parse(xml).entries[0]
    assert RSSSource()._to_item(entry, "Example Blog") is None


# --- registry --------------------------------------------------------------


def test_registry_covers_all_sources() -> None:
    from trend.sources import REGISTRY, build_source, source_weights

    assert set(REGISTRY) == {"hackernews", "github", "arxiv", "rss"}
    assert build_source("hackernews") is not None
    assert build_source("does-not-exist") is None
    # Hacker News is the most trusted signal, arXiv the least.
    weights = source_weights()
    assert weights["hackernews"] > weights["github"] > weights["rss"] > weights["arxiv"]
