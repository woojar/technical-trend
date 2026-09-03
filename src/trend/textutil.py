"""Plain-text extraction from upstream HTML.

Several sources hand back HTML in fields that are documented as text: Hacker News
``story_text`` contains anchor tags with entity-escaped attributes, and RSS
``description`` is usually an escaped HTML fragment. That markup must not reach
the rendered Markdown or the LLM prompt, where it wastes tokens and produces
output like ``&#x2F;&#x2F;`` mid-sentence.
"""

from __future__ import annotations

import html
import re

_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,;:!?)])")


def html_to_text(raw: str) -> str:
    """Reduce an HTML fragment to single-line plain text.

    Entities are unescaped before tags are stripped, because sources vary in how
    many times they escape: Hacker News sends live tags with escaped attributes,
    while some feeds send the whole fragment escaped once more.
    """
    if not raw:
        return ""

    text = _SCRIPT_RE.sub(" ", raw)
    text = html.unescape(text)
    # Unescaping may have revealed markup that was hidden as entities.
    text = _SCRIPT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)

    text = " ".join(text.split())
    # Removing an inline tag mid-sentence leaves a gap before punctuation.
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)


def excerpt(raw: str, limit: int) -> str:
    """Plain-text excerpt of at most ``limit`` characters, cut on a word boundary.

    Conversion happens before truncation so the limit counts readable
    characters rather than markup, and the ellipsis is counted against the
    limit rather than pushing the result over it.
    """
    text = html_to_text(raw)
    if len(text) <= limit:
        return text
    clipped = text[: max(limit - 3, 0)].rsplit(" ", 1)[0].rstrip(" ,;:.")
    return f"{clipped}..." if clipped else text[:limit]


#: Credential shapes worth scrubbing from anything we log. Not exhaustive by
#: design -- the exact key is redacted separately, and this only has to catch a
#: provider echoing something we did not send.
_SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_-]{16,}"),  # Google API keys
    re.compile(r"AQ\.[0-9A-Za-z_-]{16,}"),  # newer Google credential format
    re.compile(r"\b(?:gsk|sk|csk|xoxb|xoxp|ghp|gho|github_pat)[-_][0-9A-Za-z_-]{16,}"),
    re.compile(r"(?i)bearer\s+[0-9A-Za-z._-]{16,}"),
)

REDACTED = "[REDACTED]"


def redact(text: str, *secrets: str) -> str:
    """Strip credentials out of text destined for a log.

    Provider error bodies are echoed into log messages to make failures
    diagnosable, and GitHub Actions logs are world-readable on a public
    repository. Actions masks values it knows are secrets, but that does not
    cover a local run, so scrub here too rather than relying on it.

    Known secret values are removed first because that is exact; the patterns
    are a fallback for a credential we never sent but which appears anyway.
    """
    if not text:
        return text

    for secret in secrets:
        # Ignore trivially short values: replacing them would mangle the message
        # and tell an attacker nothing useful anyway.
        if secret and len(secret) >= 8:
            text = text.replace(secret, REDACTED)

    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)

    return text
