"""Recent arXiv preprints via the public Atom API.

arXiv carries no engagement signal, so items arrive with ``points=0`` and are
ranked purely on source weight and recency. That is intentional: the Research
category gets a fixed quota in the digest rather than competing with Hacker News
vote counts.

Terms of use ask for at most one request every three seconds; the delay between
category queries respects that.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

import feedparser

from trend.models import Item
from trend.sources.base import FetchContext
from trend.textutil import excerpt

log = logging.getLogger(__name__)

API = "http://export.arxiv.org/api/query"
REQUEST_DELAY_SECONDS = 3.0


class ArxivSource:
    name = "arxiv"
    weight = 0.6

    def fetch(self, ctx: FetchContext) -> list[Item]:
        categories: list[str] = list(ctx.options.get("categories") or ["cs.AI", "cs.SE"])
        per_category = int(ctx.options.get("per_category", 15))

        items: list[Item] = []
        for idx, category in enumerate(categories):
            if idx:
                time.sleep(REQUEST_DELAY_SECONDS)
            items.extend(self._fetch_category(ctx, category, per_category))

        log.info("arxiv: %d items", len(items))
        return items

    def _fetch_category(self, ctx: FetchContext, category: str, limit: int) -> list[Item]:
        params = {
            "search_query": f"cat:{category}",
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": limit,
        }
        try:
            resp = ctx.session.get(API, params=params, timeout=30)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as exc:
            log.warning("arxiv: category %s failed: %s", category, exc)
            return []

        items: list[Item] = []
        for entry in feed.entries:
            item = self._to_item(entry, category)
            # arXiv cannot filter by date server-side, so enforce the window here.
            if item is not None and ctx.window_start <= item.published <= ctx.window_end:
                items.append(item)
        return items

    def _to_item(self, entry, category: str) -> Item | None:
        title = " ".join((entry.get("title") or "").split())
        url = (entry.get("link") or "").strip()
        if not title or not url:
            return None

        published = _parse_struct_time(entry.get("published_parsed"))
        if published is None:
            return None

        authors = [a.get("name", "") for a in (entry.get("authors") or [])]

        return Item(
            title=title,
            url=url,
            source=self.name,
            published=published,
            author=", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
            excerpt=excerpt(entry.get("summary") or "", 900),
            extra={"arxiv_category": category, "authors": authors[:8]},
        )


def _parse_struct_time(parsed) -> datetime | None:
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=UTC)
    except (TypeError, ValueError):
        return None
