"""Core data structures shared across the pipeline.

Everything is a plain dataclass so it round-trips through JSON without a schema
library. ``Item`` is what sources emit, ``Cluster`` is what dedupe emits, and
``Digest`` is what the renderer consumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class Item:
    """A single story, repository, or paper picked up from one source."""

    title: str
    url: str
    source: str
    published: datetime
    #: Upstream discussion/attention signal (HN points, GitHub stars, ...).
    points: int = 0
    comments: int = 0
    author: str = ""
    #: Raw upstream text (abstract, repo description). Never model-generated.
    excerpt: str = ""
    #: Link to the discussion thread when it differs from ``url``.
    discussion_url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    #: Filled in by :mod:`trend.dedupe`.
    canonical_url: str = ""

    def __post_init__(self) -> None:
        if self.published.tzinfo is None:
            self.published = self.published.replace(tzinfo=UTC)

    def age_days(self, now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        return max((now - self.published).total_seconds() / 86400.0, 0.0)


@dataclass(slots=True)
class Cluster:
    """A group of items that all describe the same underlying story."""

    items: list[Item]
    score: float = 0.0

    @property
    def primary(self) -> Item:
        """The most authoritative item, used for title and link."""
        return max(self.items, key=lambda i: (i.points, i.comments, -i.age_days()))

    @property
    def sources(self) -> list[str]:
        seen: dict[str, None] = {}
        for item in self.items:
            seen.setdefault(item.source, None)
        return list(seen)

    @property
    def total_points(self) -> int:
        return sum(i.points for i in self.items)

    @property
    def total_comments(self) -> int:
        return sum(i.comments for i in self.items)


@dataclass(slots=True)
class Entry:
    """One rendered digest entry: a cluster plus its editorial layer."""

    cluster: Cluster
    category: str = "Other"
    headline: str = ""
    summary: str = ""
    why_it_matters: str = ""

    @property
    def title(self) -> str:
        return self.headline or self.cluster.primary.title

    @property
    def url(self) -> str:
        return self.cluster.primary.url


@dataclass(slots=True)
class Digest:
    """A full weekly issue, ready to render."""

    week: str
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    entries: list[Entry] = field(default_factory=list)
    intro: str = ""
    #: Name of the LLM provider that produced the editorial layer, or "" when
    #: the heuristic fallback was used.
    provider: str = ""
    stats: dict[str, Any] = field(default_factory=dict)

    def by_category(self, order: list[str]) -> list[tuple[str, list[Entry]]]:
        """Group entries by category, honouring the configured order."""
        buckets: dict[str, list[Entry]] = {}
        for entry in self.entries:
            buckets.setdefault(entry.category, []).append(entry)

        result = [(c, buckets.pop(c)) for c in order if c in buckets]
        # Any category the model invented lands at the end, alphabetically.
        result.extend(sorted(buckets.items()))
        return result
