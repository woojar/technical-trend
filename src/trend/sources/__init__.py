"""Source registry.

``config.yaml`` keys map to the implementations here. A source that raises is
logged and skipped so one dead feed cannot fail the whole weekly run.
"""

from __future__ import annotations

import logging

from trend.sources.arxiv import ArxivSource
from trend.sources.base import FetchContext, Source
from trend.sources.github import GitHubSource
from trend.sources.hackernews import HackerNewsSource
from trend.sources.rss import RSSSource

log = logging.getLogger(__name__)

REGISTRY: dict[str, type] = {
    "hackernews": HackerNewsSource,
    "github": GitHubSource,
    "arxiv": ArxivSource,
    "rss": RSSSource,
}

__all__ = [
    "REGISTRY",
    "ArxivSource",
    "FetchContext",
    "GitHubSource",
    "HackerNewsSource",
    "RSSSource",
    "Source",
    "build_source",
    "source_weights",
]


def build_source(name: str) -> Source | None:
    cls = REGISTRY.get(name)
    if cls is None:
        log.warning("unknown source %r in config; skipping", name)
        return None
    return cls()


def source_weights() -> dict[str, float]:
    """Default weight per registered source name."""
    return {name: float(cls.weight) for name, cls in REGISTRY.items()}
