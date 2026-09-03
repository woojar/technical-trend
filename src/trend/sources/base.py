"""Source plugin interface.

Each source owns its own notion of relevance and returns only the items it
considers in-window. The orchestrator does not re-filter by date, because
"trending" means different things per source: a Hacker News story is trending
when it was *posted* this week, while a GitHub repository is trending when it
gained stars this week despite being created months ago.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import requests

from trend.models import Item


@dataclass(slots=True)
class FetchContext:
    """Everything a source needs to do its job."""

    session: requests.Session
    window_start: datetime
    window_end: datetime
    #: Per-source block from ``config.yaml``.
    options: dict[str, Any]


class Source(Protocol):
    """A named fetcher of :class:`~trend.models.Item` objects."""

    name: str
    #: Relative trust, used by the ranker to compare across sources.
    weight: float

    def fetch(self, ctx: FetchContext) -> list[Item]:
        """Return in-window items, or an empty list if the source is unusable."""
        ...
