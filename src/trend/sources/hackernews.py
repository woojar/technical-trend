"""Hacker News via the Algolia search API.

Algolia is used instead of the official Firebase API because it supports a
time-window plus minimum-score query in one request. The Firebase API would
require fetching and filtering thousands of individual items.

No key, no auth, documented at https://hn.algolia.com/api
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from trend.http import get_json
from trend.models import Item
from trend.sources.base import FetchContext
from trend.textutil import excerpt

log = logging.getLogger(__name__)

API = "https://hn.algolia.com/api/v1/search_by_date"
ITEM_URL = "https://news.ycombinator.com/item?id={}"


class HackerNewsSource:
    name = "hackernews"
    weight = 1.0

    def fetch(self, ctx: FetchContext) -> list[Item]:
        min_points = int(ctx.options.get("min_points", 100))
        limit = int(ctx.options.get("limit", 150))
        start_ts = int(ctx.window_start.timestamp())
        end_ts = int(ctx.window_end.timestamp())

        items: list[Item] = []
        page = 0
        per_page = min(100, limit)

        while len(items) < limit and page < 10:
            params = {
                "tags": "story",
                "numericFilters": (
                    f"created_at_i>{start_ts},created_at_i<{end_ts},points>{min_points}"
                ),
                "hitsPerPage": per_page,
                "page": page,
            }
            try:
                data = get_json(ctx.session, API, params=params)
            except Exception as exc:
                log.warning("hackernews: fetch failed on page %d: %s", page, exc)
                break

            hits = data.get("hits") or []
            if not hits:
                break

            for hit in hits:
                item = self._to_item(hit)
                if item is not None:
                    items.append(item)

            if page >= int(data.get("nbPages", 1)) - 1:
                break
            page += 1

        log.info("hackernews: %d items", len(items))
        return items[:limit]

    def _to_item(self, hit: dict) -> Item | None:
        title = (hit.get("title") or "").strip()
        object_id = hit.get("objectID")
        if not title or not object_id:
            return None

        discussion = ITEM_URL.format(object_id)
        # "Ask HN"/"Show HN" text posts have no external URL; link the thread.
        url = (hit.get("url") or "").strip() or discussion

        created = hit.get("created_at_i")
        published = datetime.fromtimestamp(int(created), tz=UTC) if created else datetime.now(UTC)

        return Item(
            title=title,
            url=url,
            source=self.name,
            published=published,
            points=int(hit.get("points") or 0),
            comments=int(hit.get("num_comments") or 0),
            author=(hit.get("author") or "").strip(),
            # story_text is HTML with entity-escaped attributes, not plain text.
            excerpt=excerpt(hit.get("story_text") or "", 600),
            discussion_url=discussion,
            extra={"hn_id": str(object_id)},
        )
