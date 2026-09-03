"""Markdown rendering.

Markdown because it renders natively on GitHub, diffs cleanly in git, and
converts to anything else later. Every entry keeps its source links so a claim in
the digest can always be traced back to the original.
"""

from __future__ import annotations

from trend.models import Digest, Entry

_SOURCE_LABELS = {
    "hackernews": "Hacker News",
    "github": "GitHub",
    "arxiv": "arXiv",
}


def source_label(source: str) -> str:
    if source.startswith("rss:"):
        return source[4:]
    return _SOURCE_LABELS.get(source, source)


def _entry_markdown(entry: Entry, index: int) -> str:
    cluster = entry.cluster
    primary = cluster.primary
    lines = [f"### {index}. [{entry.title}]({primary.url})", ""]

    meta: list[str] = [source_label(primary.source)]
    if primary.source == "github":
        stars = primary.extra.get("stars")
        language = primary.extra.get("language")
        if stars:
            meta.append(f"{stars:,} stars")
        if language:
            meta.append(language)
    elif primary.points:
        meta.append(f"{primary.points:,} points")
    if primary.comments and primary.source != "github":
        meta.append(f"{primary.comments:,} comments")
    meta.append(primary.published.strftime("%Y-%m-%d"))

    lines.append("*" + " · ".join(meta) + "*")
    lines.append("")

    if entry.summary:
        lines.extend([entry.summary, ""])
    if entry.why_it_matters:
        lines.extend([f"**Why it matters:** {entry.why_it_matters}", ""])

    links = []
    if primary.discussion_url and primary.discussion_url != primary.url:
        links.append(f"[Discussion]({primary.discussion_url})")
    # Corroborating coverage from other sources, so the reader can compare.
    for item in cluster.items:
        if item is primary or item.url == primary.url:
            continue
        links.append(f"[{source_label(item.source)}]({item.url})")
    if links:
        lines.extend([" · ".join(links[:5]), ""])

    return "\n".join(lines)


def render_markdown(digest: Digest, categories: list[str]) -> str:
    start = digest.window_start.strftime("%Y-%m-%d")
    end = digest.window_end.strftime("%Y-%m-%d")

    out: list[str] = [
        f"# Tech Trends — {digest.week}",
        "",
        f"*{start} to {end} · {len(digest.entries)} stories*",
        "",
    ]

    if digest.intro:
        out.extend([digest.intro, ""])

    grouped = digest.by_category(categories)

    if len(grouped) > 1:
        out.extend(["## Contents", ""])
        for category, entries in grouped:
            out.append(f"- [{category}](#{_anchor(category)}) ({len(entries)})")
        out.append("")

    counter = 1
    for category, entries in grouped:
        out.extend([f"## {category}", ""])
        for entry in entries:
            out.append(_entry_markdown(entry, counter))
            counter += 1

    out.extend(["---", ""])
    out.append(_footer(digest))
    out.append("")
    return "\n".join(out)


def _footer(digest: Digest) -> str:
    generated = digest.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    parts = [f"Generated {generated}"]

    stats = digest.stats
    if stats.get("fetched"):
        parts.append(
            f"{stats['fetched']} items fetched, {stats.get('clustered', 0)} unique stories"
        )
    # State the degraded path explicitly rather than letting it pass unnoticed.
    parts.append(
        f"summarized by {digest.provider}"
        if digest.provider
        else "no LLM available — headlines and excerpts only"
    )
    return "*" + " · ".join(parts) + ".*"


def _anchor(text: str) -> str:
    """GitHub-style heading anchor."""
    slug = "".join(c if c.isalnum() or c in " -" else "" for c in text.lower())
    return slug.strip().replace(" ", "-")


def render_summary_text(digest: Digest, limit: int = 8) -> str:
    """Short plain-text form for webhook notifications."""
    lines = [f"Tech Trends {digest.week} — {len(digest.entries)} stories"]
    if digest.intro:
        lines.extend(["", digest.intro])
    lines.append("")
    for i, entry in enumerate(digest.entries[:limit], 1):
        lines.append(f"{i}. {entry.title}\n   {entry.url}")
    if len(digest.entries) > limit:
        lines.append(f"\n...and {len(digest.entries) - limit} more.")
    return "\n".join(lines)
