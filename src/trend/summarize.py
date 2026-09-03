"""The editorial layer: categorize, headline and summarize the selected stories.

Two design choices are driven by free-tier limits:

**Batching.** Items are summarized in groups rather than one request each. A
30-item digest costs 3 calls instead of 30, which keeps a weekly run inside even
the tightest requests-per-day allowance.

**Graceful degradation.** If every provider fails, the pipeline still produces a
digest using keyword categorization and upstream excerpts. A weekly job that
silently stops publishing is worse than one that publishes something plainer.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from trend.llm.base import LLMError
from trend.llm.router import Router
from trend.models import Cluster, Entry
from trend.textutil import excerpt

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the editor of a weekly digest for professional software engineers.

For each input item produce:
- category: exactly one value from the provided category list
- headline: a clear, factual headline, max 12 words, no clickbait, no emoji
- summary: 1-2 sentences on what actually happened, concrete and specific
- why_it_matters: one sentence on the practical consequence for engineers

Rules:
- Use only the information given. Never invent versions, benchmarks, names or dates.
- If an item is too vague to summarize, reuse its title as the headline and
  state plainly what is unclear.
- Neutral, technical register. No marketing language, no hype, no exclamation marks.
- Reply with JSON only, no prose and no code fences.

Response shape:
{"items": [
  {"index": <int>, "category": "<category>", "headline": "...",
   "summary": "...", "why_it_matters": "..."}
]}
Return exactly one object per input index."""

INTRO_SYSTEM_PROMPT = """You write the opening paragraph of a weekly engineering digest.

Given this week's headlines, write 2-3 sentences naming the genuine themes that
connect them. Be specific and factual, no hype, no lists, no emoji. If there is
no real common thread, say the week was varied rather than inventing a narrative.
Reply with the paragraph text only."""

#: Keyword buckets for the no-LLM fallback path. Ordered: first match wins.
_FALLBACK_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "Security",
        (
            "cve",
            "vulnerabilit",
            "exploit",
            "breach",
            "malware",
            "ransomware",
            "backdoor",
            "phishing",
            "zero-day",
            "supply chain",
        ),
    ),
    (
        "AI & Machine Learning",
        (
            "llm",
            "gpt",
            "claude",
            "gemini",
            "transformer",
            "diffusion",
            "neural",
            "machine learning",
            " ai ",
            "inference",
            "embedding",
            "rag",
            "agent",
            "fine-tun",
            "model",
        ),
    ),
    (
        "Languages & Runtimes",
        (
            "python",
            "rust",
            "golang",
            " go ",
            "java",
            "typescript",
            "javascript",
            "c++",
            "zig",
            "compiler",
            "runtime",
            "jvm",
            "interpreter",
            "wasm",
            "webassembly",
        ),
    ),
    (
        "Infrastructure & Cloud",
        (
            "kubernetes",
            "docker",
            "container",
            "aws",
            "azure",
            "gcp",
            "cloud",
            "serverless",
            "terraform",
            "database",
            "postgres",
            "sql",
            "kafka",
            "distributed",
            "observability",
        ),
    ),
    (
        "Developer Tools",
        (
            "editor",
            "vscode",
            "vim",
            "neovim",
            "git",
            "ci/cd",
            "build",
            "debugger",
            "linter",
            "framework",
            "library",
            "cli",
            "sdk",
        ),
    ),
    (
        "Research",
        (
            "we propose",
            "we present",
            "benchmark",
            "empirical",
            "we study",
            "this paper",
            "novel approach",
        ),
    ),
]


def _cluster_payload(clusters: list[Cluster], offset: int) -> list[dict[str, Any]]:
    """Compact per-cluster JSON for the prompt.

    Excerpts are truncated hard: on free tiers the context window and per-minute
    token budget are the binding constraints, and the first few hundred
    characters of an abstract carry most of the meaning.
    """
    payload = []
    for idx, cluster in enumerate(clusters):
        primary = cluster.primary
        entry: dict[str, Any] = {
            "index": offset + idx,
            "title": primary.title[:250],
            "source": primary.source,
            "url": primary.url,
        }
        if primary.excerpt:
            entry["excerpt"] = primary.excerpt[:500]
        if primary.points:
            entry["attention"] = primary.points
        if len(cluster.sources) > 1:
            entry["also_covered_by"] = cluster.sources[1:]
        lang = primary.extra.get("language")
        if lang:
            entry["language"] = lang
        payload.append(entry)
    return payload


def _fallback_category(cluster: Cluster, categories: list[str]) -> str:
    haystack = f" {cluster.primary.title.lower()} {cluster.primary.excerpt[:300].lower()} "
    if cluster.primary.source == "arxiv" and "Research" in categories:
        return "Research"
    for category, keywords in _FALLBACK_RULES:
        if category in categories and any(k in haystack for k in keywords):
            return category
    return categories[-1] if categories else "Other"


def _fallback_entry(cluster: Cluster, categories: list[str]) -> Entry:
    primary = cluster.primary
    return Entry(
        cluster=cluster,
        category=_fallback_category(cluster, categories),
        headline=primary.title,
        summary=excerpt(primary.excerpt, 280),
        why_it_matters="",
    )


def _batches(clusters: list[Cluster], size: int) -> list[list[Cluster]]:
    size = max(size, 1)
    return [clusters[i : i + size] for i in range(0, len(clusters), size)]


def summarize(
    clusters: list[Cluster],
    categories: list[str],
    router: Router,
    *,
    batch_size: int = 12,
) -> tuple[list[Entry], str]:
    """Build entries for ``clusters``.

    Returns ``(entries, provider_name)``. ``provider_name`` is empty when the
    heuristic fallback produced everything, which the renderer surfaces so a
    degraded issue is never mistaken for a normal one.
    """
    if not clusters:
        return [], ""

    if not router.has_available:
        log.warning("llm: no provider configured; using heuristic summaries")
        return [_fallback_entry(c, categories) for c in clusters], ""

    entries: list[Entry | None] = [None] * len(clusters)
    provider_used = ""
    offset = 0

    for batch in _batches(clusters, batch_size):
        user_prompt = json.dumps(
            {
                "categories": categories,
                "items": _cluster_payload(batch, offset),
            },
            ensure_ascii=False,
            indent=None,
        )

        try:
            data = router.complete_json(SYSTEM_PROMPT, user_prompt)
            provider_used = router.last_used or provider_used
            for record in _iter_records(data):
                idx = record.get("index")
                if not isinstance(idx, int) or not 0 <= idx < len(clusters):
                    continue
                cluster = clusters[idx]
                category = str(record.get("category") or "").strip()
                entries[idx] = Entry(
                    cluster=cluster,
                    # Reject invented categories so grouping stays stable.
                    category=category
                    if category in categories
                    else _fallback_category(cluster, categories),
                    headline=str(record.get("headline") or "").strip() or cluster.primary.title,
                    summary=str(record.get("summary") or "").strip(),
                    why_it_matters=str(record.get("why_it_matters") or "").strip(),
                )
        except (LLMError, ValueError, TypeError) as exc:
            log.warning("llm: batch at offset %d failed (%s); using fallback", offset, exc)

        offset += len(batch)

    # Any item the model skipped or mis-indexed still gets an entry.
    missing = sum(1 for e in entries if e is None)
    if missing:
        log.info("llm: %d/%d items fell back to heuristic summaries", missing, len(clusters))

    final = [
        entry if entry is not None else _fallback_entry(clusters[i], categories)
        for i, entry in enumerate(entries)
    ]
    return final, provider_used


def _iter_records(data: Any) -> list[dict[str, Any]]:
    """Accept the documented shape and the common deviations from it."""
    if isinstance(data, dict):
        for key in ("items", "results", "entries", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        # A single-object reply when the batch had one item.
        return [data] if "index" in data else []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def write_intro(entries: list[Entry], router: Router) -> str:
    """One extra call for the opening paragraph. Returns "" on any failure."""
    if not entries or not router.has_available:
        return ""

    headlines = [f"- [{e.category}] {e.title}" for e in entries[:20]]
    try:
        completion = router.complete(INTRO_SYSTEM_PROMPT, "\n".join(headlines))
    except LLMError as exc:
        log.warning("llm: intro generation failed (%s); omitting", exc)
        return ""
    return " ".join(completion.text.split())
