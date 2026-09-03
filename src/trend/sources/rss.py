"""Generic RSS/Atom source -- the extension point for everything else.

Adding a vendor engineering blog, Lobste.rs, or an RSSHub-generated feed is a
config change, not a code change. Feeds without engagement metrics rank on
source weight and recency alone.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import feedparser

from trend.models import Item
from trend.sources.base import FetchContext
from trend.textutil import excerpt

log = logging.getLogger(__name__)


class RSSSource:
    name = "rss"
    weight = 0.7

    def fetch(self, ctx: FetchContext) -> list[Item]:
        feeds: list[dict] = list(ctx.options.get("feeds") or [])
        per_feed = int(ctx.options.get("per_feed", 10))

        items: list[Item] = []
        for feed_cfg in feeds:
            url = (feed_cfg.get("url") or "").strip()
            if not url:
                continue
            label = (feed_cfg.get("name") or url).strip()
            items.extend(self._fetch_feed(ctx, url, label, per_feed))

        log.info("rss: %d items from %d feeds", len(items), len(feeds))
        return items

    def _fetch_feed(self, ctx: FetchContext, url: str, label: str, limit: int) -> list[Item]:
        try:
            resp = ctx.session.get(url, timeout=25)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as exc:
            log.warning("rss: feed %s failed: %s", label, exc)
            return []

        items: list[Item] = []
        for entry in feed.entries[: limit * 3]:
            if len(items) >= limit:
                break
            item = self._to_item(entry, label)
            if item is None:
                continue
            # Many feeds omit or mangle dates; skip anything outside the window
            # rather than guessing, otherwise old posts leak into every digest.
            if ctx.window_start <= item.published <= ctx.window_end:
                items.append(item)
        return items

    def _to_item(self, entry, label: str) -> Item | None:
        title = " ".join((entry.get("title") or "").split())
        url = (entry.get("link") or "").strip()
        if not title or not url:
            return None

        published = _entry_datetime(entry)
        if published is None:
            return None

        return Item(
            title=title,
            url=url,
            source=f"rss:{label}",
            published=published,
            author=(entry.get("author") or "").strip(),
            excerpt=excerpt(entry.get("summary") or "", 600),
            extra={"feed": label},
        )


def _entry_datetime(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=UTC)
            except (TypeError, ValueError):
                continue
    return None
