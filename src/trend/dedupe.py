"""URL canonicalization and story clustering.

This is the highest-leverage part of the pipeline: the same story typically
appears on Hacker News, a vendor blog, and two aggregator feeds within a week.
Summarizing all four wastes scarce free-tier LLM quota and makes the digest
read like spam.

Two passes:

1. Exact match on a canonical URL (tracking params stripped, host normalized).
2. Fuzzy match on title token overlap, which catches the case where outlets
   link to different copies of the same announcement.

Deliberately dependency-free -- no embeddings, no network. Token-set Jaccard on
normalized titles is crude but predictable, and predictability matters more than
recall for an unattended weekly job.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from trend.models import Cluster, Item

#: Query parameters that never identify content.
_TRACKING_PREFIXES = ("utm_", "mc_", "pk_", "ga_", "_hs")
_TRACKING_EXACT = frozenset(
    {
        "ref",
        "ref_src",
        "referrer",
        "source",
        "fbclid",
        "gclid",
        "igshid",
        "mkt_tok",
        "spm",
        "share",
        "share_id",
        "cmpid",
        "at_medium",
        "at_campaign",
    }
)

#: Host prefixes that are pure mirrors of the canonical host.
_HOST_PREFIXES = ("www.", "m.", "amp.", "mobile.")

#: Words that carry no signal when comparing headlines. Written as a block of
#: prose for readability; SIM905 would have it as a 60-element list literal.
_STOPWORDS = frozenset(
    """
    a an the and or but of to in on for with without from by at as is are was were be been
    being this that these those it its it's we you they i how why what when which who whom
    new now show hn tell introducing introduce announcing announce released release releases
    launch launches launched update updates version v1 v2 v3 using use used via into over
    about your our their my more most just can could will would than then
    way ways guide tutorial part deep dive look inside
    """.split()  # noqa: SIM905
)

_WORD_RE = re.compile(r"[a-z0-9+#.]+")


def canonicalize_url(url: str) -> str:
    """Reduce a URL to a stable identity key.

    Strips tracking parameters, mirror host prefixes, default ports, fragments
    and trailing slashes. Remaining query parameters are sorted so that
    parameter order does not create false distinctions.
    """
    if not url:
        return ""

    raw = url.strip()
    if "//" not in raw:
        raw = "https://" + raw.lstrip("/")

    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    for prefix in _HOST_PREFIXES:
        if host.startswith(prefix) and len(host) > len(prefix) + 3:
            host = host[len(prefix) :]
            break

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_EXACT and not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    query = urlencode(sorted(kept))

    path = re.sub(r"/+", "/", parts.path)
    if len(path) > 1:
        path = path.rstrip("/")
    # index.html and friends are the same resource as the bare directory.
    path = re.sub(r"/(index|default)\.(html?|php|aspx?)$", "", path)

    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme
    return urlunsplit((scheme, host, path, query, ""))


def title_tokens(title: str) -> frozenset[str]:
    """Content-bearing lowercase tokens of a headline."""
    words = _WORD_RE.findall(title.lower())
    return frozenset(w for w in words if w not in _STOPWORDS and len(w) > 1)


def title_similarity(a: str, b: str) -> float:
    """Jaccard similarity of the two token sets, in ``[0.0, 1.0]``."""
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def cluster_items(items: list[Item], threshold: float = 0.6) -> list[Cluster]:
    """Group items describing the same story.

    ``threshold`` is the minimum title similarity for a fuzzy merge. Raise it to
    make the digest more permissive of near-duplicates, lower it to merge more
    aggressively at the risk of collapsing distinct stories.
    """
    for item in items:
        if not item.canonical_url:
            item.canonical_url = canonicalize_url(item.url)

    # Pass 1: exact canonical URL.
    by_url: dict[str, list[Item]] = {}
    for item in items:
        # Items with no usable URL each get their own bucket.
        key = item.canonical_url or f"\0{id(item)}"
        by_url.setdefault(key, []).append(item)

    # Pass 2: fuzzy title merge across buckets. Greedy single pass against
    # already-formed groups; O(n*groups), fine for the few hundred items a week
    # produces.
    groups: list[list[Item]] = []
    group_tokens: list[frozenset[str]] = []

    for bucket in by_url.values():
        bucket_title = max(bucket, key=lambda i: i.points).title
        tokens = title_tokens(bucket_title)

        best_idx, best_score = -1, 0.0
        for idx, existing in enumerate(group_tokens):
            if not tokens or not existing:
                continue
            score = len(tokens & existing) / len(tokens | existing)
            if score >= threshold and score > best_score:
                best_idx, best_score = idx, score

        if best_idx >= 0:
            groups[best_idx].extend(bucket)
            group_tokens[best_idx] = group_tokens[best_idx] | tokens
        else:
            groups.append(list(bucket))
            group_tokens.append(tokens)

    return [Cluster(items=g) for g in groups]
