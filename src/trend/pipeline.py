"""Pipeline orchestration: fetch, dedupe, rank, summarize, render.

Kept separate from the CLI so the whole flow is callable from tests or another
program without going through argument parsing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trend.config import Config
from trend.dedupe import canonicalize_url, cluster_items
from trend.http import build_llm_session, build_session
from trend.llm.router import Router
from trend.models import Cluster, Digest, Item
from trend.rank import score_clusters
from trend.render import render_markdown
from trend.sources import build_source, source_weights
from trend.sources.base import FetchContext
from trend.store import Store
from trend.summarize import summarize, write_intro

log = logging.getLogger(__name__)


def iso_week(dt: datetime) -> str:
    """ISO week label, e.g. ``2026-W36``. Sorts lexicographically by date."""
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


@dataclass(slots=True)
class FetchResult:
    items: list[Item]
    per_source: dict[str, int]
    window_start: datetime
    window_end: datetime


def fetch_items(cfg: Config, *, now: datetime | None = None) -> FetchResult:
    """Run every enabled source. A failing source is skipped, not fatal."""
    now = now or datetime.now(UTC)
    window_start = now - timedelta(days=cfg.window_days)
    session = build_session()

    items: list[Item] = []
    per_source: dict[str, int] = {}

    for name in cfg.sources:
        if not cfg.source_enabled(name):
            log.debug("source %s disabled", name)
            continue
        source = build_source(name)
        if source is None:
            continue

        ctx = FetchContext(
            session=session,
            window_start=window_start,
            window_end=now,
            options=cfg.source_cfg(name),
        )
        try:
            fetched = source.fetch(ctx)
        except Exception as exc:
            log.warning("source %s failed entirely: %s", name, exc)
            continue

        per_source[name] = len(fetched)
        items.extend(fetched)

    log.info("fetched %d items from %d sources", len(items), len(per_source))
    return FetchResult(
        items=items,
        per_source=per_source,
        window_start=window_start,
        window_end=now,
    )


def _apply_category_quota(clusters: list[Cluster], cfg: Config) -> list[Cluster]:
    """Placeholder-free pre-LLM cap.

    Category is only known after summarization, so the quota is applied later in
    :func:`build_digest`. This trims the pre-LLM candidate set to a multiple of
    the target size, which is what actually controls token spend.
    """
    budget = max(cfg.max_entries * 2, cfg.max_entries)
    return clusters[:budget]


def build_digest(cfg: Config, *, now: datetime | None = None, dry_run: bool = False) -> Digest:
    """Produce a full digest without writing anything to disk."""
    now = now or datetime.now(UTC)
    result = fetch_items(cfg, now=now)

    store: Store | None = None
    filtered = result.items
    # A dry run reads existing state so the preview matches what a real run
    # would publish, but never creates the database and never records anything.
    if cfg.skip_seen and (not dry_run or cfg.state_db.is_file()):
        store = Store(cfg.state_db)
        seen = store.seen_urls()
        if seen:
            filtered = [i for i in result.items if canonicalize_url(i.url) not in seen]
            log.info("skipped %d previously published items", len(result.items) - len(filtered))

    clusters = cluster_items(filtered, threshold=cfg.dedupe_threshold)
    log.info("clustered %d items into %d stories", len(filtered), len(clusters))

    score_clusters(clusters, source_weights(), window_days=cfg.window_days, now=now)
    candidates = _apply_category_quota(clusters, cfg)[: cfg.max_entries]

    router = Router.from_config(cfg.llm_chain, session=build_llm_session())
    entries, provider = summarize(candidates, cfg.categories, router, batch_size=cfg.batch_size)

    # Enforce the per-category cap now that categories are known, preserving
    # score order within each bucket.
    if cfg.max_per_category > 0:
        counts: dict[str, int] = {}
        kept = []
        for entry in entries:
            n = counts.get(entry.category, 0)
            if n >= cfg.max_per_category:
                continue
            counts[entry.category] = n + 1
            kept.append(entry)
        if len(kept) < len(entries):
            log.info("per-category cap dropped %d entries", len(entries) - len(kept))
        entries = kept

    intro = write_intro(entries, router) if provider else ""

    digest = Digest(
        week=iso_week(now),
        generated_at=now,
        window_start=result.window_start,
        window_end=result.window_end,
        entries=entries,
        intro=intro,
        provider=provider,
        stats={
            "fetched": len(result.items),
            "after_seen_filter": len(filtered),
            "clustered": len(clusters),
            "per_source": result.per_source,
            "llm_providers": [
                {"name": n, "model": m, "configured": c} for n, m, c in router.describe()
            ],
        },
    )

    if store is not None and entries and not dry_run:
        store.mark_seen(
            ((canonicalize_url(i.url), i.title) for e in entries for i in e.cluster.items),
            digest.week,
        )

    return digest


def write_digest(digest: Digest, cfg: Config) -> Path:
    """Write the rendered digest and refresh ``latest.md``."""
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.output_dir / f"{digest.week}.md"
    content = render_markdown(digest, cfg.categories)
    path.write_text(content, encoding="utf-8")

    # Stable path for linking from the README.
    (cfg.output_dir / "latest.md").write_text(content, encoding="utf-8")
    log.info("wrote %s", path)
    return path
