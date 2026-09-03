"""Cluster scoring.

Raw engagement numbers are not comparable across sources: a strong Hacker News
story gets ~500 points, a strong GitHub repo ~20000 stars, and an arXiv paper
gets nothing at all. Ranking on raw values would make the digest all GitHub.

So attention is normalized *within* each source to ``[0, 1]`` first, using a log
scale because engagement is heavy-tailed -- the gap between 50 and 500 points
matters far more than between 5000 and 5450.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from trend.models import Cluster

#: Score composition. Attention dominates, but source weight keeps low-signal
#: sources such as arXiv representable, and recency breaks ties.
W_ATTENTION = 0.60
W_SOURCE = 0.25
W_RECENCY = 0.15

#: Added per *additional* distinct source in a cluster. A story that surfaced on
#: both Hacker News and a vendor blog is a stronger signal than either alone.
CROSS_SOURCE_BONUS = 0.12


def _normalize_attention(clusters: list[Cluster]) -> dict[int, float]:
    """Log-scaled attention per cluster, normalized within its primary source."""
    raw: dict[int, tuple[str, float]] = {}
    for cluster in clusters:
        primary = cluster.primary
        # Comments are weighted lower than points: a flame war is not importance.
        signal = cluster.total_points + 0.5 * cluster.total_comments
        raw[id(cluster)] = (primary.source, math.log1p(max(signal, 0.0)))

    peak: dict[str, float] = {}
    for source, value in raw.values():
        peak[source] = max(peak.get(source, 0.0), value)

    return {
        key: (value / peak[source] if peak.get(source, 0.0) > 0 else 0.0)
        for key, (source, value) in raw.items()
    }


def score_clusters(
    clusters: list[Cluster],
    source_weights: dict[str, float],
    *,
    window_days: int = 7,
    now: datetime | None = None,
) -> list[Cluster]:
    """Assign ``cluster.score`` in place and return the list sorted descending."""
    now = now or datetime.now(UTC)
    attention = _normalize_attention(clusters)
    half_life = max(float(window_days), 1.0)

    for cluster in clusters:
        primary = cluster.primary

        # rss:<feed> entries fall back to the generic "rss" weight.
        source = primary.source
        weight = source_weights.get(source)
        if weight is None and ":" in source:
            weight = source_weights.get(source.split(":", 1)[0])
        weight = weight if weight is not None else 0.5

        recency = math.exp(-primary.age_days(now) / half_life)
        extra_sources = max(len(cluster.sources) - 1, 0)

        cluster.score = (
            W_ATTENTION * attention.get(id(cluster), 0.0)
            + W_SOURCE * weight
            + W_RECENCY * recency
            + CROSS_SOURCE_BONUS * extra_sources
        )

    clusters.sort(key=lambda c: c.score, reverse=True)
    return clusters


def select(clusters: list[Cluster], limit: int) -> list[Cluster]:
    """Top ``limit`` clusters, assuming ``clusters`` is already scored."""
    return clusters[:limit]
