"""Trending GitHub repositories via the Search API.

GitHub has no official trending API -- github.com/trending is HTML only and its
markup changes without notice. The Search API is a stable proxy: repositories
created recently that have already accumulated stars are, in practice, the ones
trending. ``created_within_days`` defaults wider than the digest window because
a repo published three days ago rarely has enough stars to rank yet.

Works unauthenticated at 10 requests/minute. Setting ``GITHUB_TOKEN`` raises
that to 30/minute; inside GitHub Actions the automatic token is enough.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

from trend.http import get_json
from trend.models import Item
from trend.sources.base import FetchContext

log = logging.getLogger(__name__)

API = "https://api.github.com/search/repositories"


class GitHubSource:
    name = "github"
    weight = 0.9

    def fetch(self, ctx: FetchContext) -> list[Item]:
        created_within = int(ctx.options.get("created_within_days", 90))
        min_stars = int(ctx.options.get("min_stars", 150))
        per_query = int(ctx.options.get("per_query", 25))
        languages: list[str] = list(ctx.options.get("languages") or [])

        since = (ctx.window_end - timedelta(days=created_within)).date().isoformat()
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # One broad query plus one per configured language, so a popular Rust
        # crate is not crowded out by whatever JavaScript did that month.
        queries = [f"created:>{since} stars:>{min_stars}"]
        queries += [f"created:>{since} stars:>{min_stars} language:{lang}" for lang in languages]

        items: list[Item] = []
        seen_ids: set[int] = set()

        for query in queries:
            params = {"q": query, "sort": "stars", "order": "desc", "per_page": per_query}
            try:
                data = get_json(ctx.session, API, params=params, headers=headers)
            except Exception as exc:
                log.warning("github: query %r failed: %s", query, exc)
                continue

            for repo in data.get("items") or []:
                repo_id = repo.get("id")
                if repo_id in seen_ids:
                    continue
                item = self._to_item(repo)
                if item is not None:
                    seen_ids.add(repo_id)
                    items.append(item)

        log.info("github: %d items", len(items))
        return items

    def _to_item(self, repo: dict) -> Item | None:
        full_name = (repo.get("full_name") or "").strip()
        url = (repo.get("html_url") or "").strip()
        if not full_name or not url:
            return None

        description = (repo.get("description") or "").strip()
        language = (repo.get("language") or "").strip()
        stars = int(repo.get("stargazers_count") or 0)

        created_raw = repo.get("created_at") or ""
        try:
            published = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            published = datetime.now(UTC)

        # Include the description in the title so dedupe and the LLM have
        # something to work with -- bare "owner/repo" carries almost no signal.
        title = f"{full_name}: {description}" if description else full_name

        return Item(
            title=title[:300],
            url=url,
            source=self.name,
            published=published,
            points=stars,
            comments=int(repo.get("open_issues_count") or 0),
            author=(repo.get("owner") or {}).get("login", ""),
            excerpt=description[:600],
            extra={
                "repo": full_name,
                "language": language,
                "stars": stars,
                "forks": int(repo.get("forks_count") or 0),
                "topics": list(repo.get("topics") or [])[:8],
            },
        )
