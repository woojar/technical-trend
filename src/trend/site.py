"""Static site generation for GitHub Pages.

Builds HTML from the digests already committed under ``digests/`` rather than
changing how they are written. Two reasons for that direction:

* The Markdown stays clean in the repository. Jekyll needs YAML front matter to
  process a page, and GitHub's Markdown viewer renders front matter as a table
  at the top of every file, so adding it would trade a readable archive for a
  buildable one.
* Historical issues are published without needing to be rewritten. Anything in
  ``digests/`` becomes a page, including issues written before this module
  existed.

Deliberately not Jekyll: no Ruby toolchain in CI, and the output is a handful of
files whose exact shape is easy to test offline.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

SITE_TITLE = "Tech Trends"
SITE_DESCRIPTION = "A weekly digest of what happened across Hacker News, GitHub, arXiv and more."

#: Files under digests/ that are not themselves issues.
_NOT_ISSUES = frozenset({"latest", "index", "readme"})

#: Matches the subtitle the Markdown renderer emits, e.g.
#: "*2026-08-27 to 2026-09-03 · 24 stories*"
_SUBTITLE_RE = re.compile(
    r"^\*(?P<start>\d{4}-\d{2}-\d{2}) to (?P<end>\d{4}-\d{2}-\d{2})"
    r"\s*·\s*(?P<count>\d+) stories\*\s*$",
    re.M,
)

#: Matches an ISO week stem such as "2026-W36".
_WEEK_RE = re.compile(r"^(?P<year>\d{4})-W(?P<week>\d{2})$")

#: The footer line records whether a model wrote the issue.
_DEGRADED_MARKER = "no LLM available"


@dataclass(slots=True)
class Issue:
    """One published digest, ready to render as a page."""

    week: str
    source: Path
    markdown: str
    start: str = ""
    end: str = ""
    story_count: int = 0
    degraded: bool = False

    @property
    def slug(self) -> str:
        return self.week

    @property
    def output_name(self) -> str:
        return f"{self.slug}.html"

    @property
    def title(self) -> str:
        return f"{SITE_TITLE} — {self.week}"

    @property
    def date_range(self) -> str:
        return f"{self.start} to {self.end}" if self.start and self.end else ""

    @property
    def sort_key(self) -> tuple[int, int]:
        """Chronological key from the ISO week, for newest-first ordering."""
        match = _WEEK_RE.match(self.week)
        if match:
            return int(match["year"]), int(match["week"])
        return (0, 0)


def discover_issues(digest_dir: Path) -> list[Issue]:
    """Read every issue under ``digest_dir``, newest first.

    ``latest.md`` is skipped because it is a duplicate of the newest issue and
    would otherwise appear twice in the archive.
    """
    if not digest_dir.is_dir():
        return []

    issues: list[Issue] = []
    for path in sorted(digest_dir.glob("*.md")):
        if path.stem.lower() in _NOT_ISSUES:
            continue
        text = path.read_text(encoding="utf-8")
        issue = Issue(week=path.stem, source=path, markdown=text)

        match = _SUBTITLE_RE.search(text)
        if match:
            issue.start = match["start"]
            issue.end = match["end"]
            issue.story_count = int(match["count"])
        issue.degraded = _DEGRADED_MARKER in text

        issues.append(issue)

    issues.sort(key=lambda i: (i.sort_key, i.week), reverse=True)
    log.info("site: found %d issue(s) in %s", len(issues), digest_dir)
    return issues


def _markdown_to_html(text: str) -> str:
    """Convert digest Markdown to an HTML fragment.

    Imported lazily so that the core pipeline, which never builds the site, does
    not require the docs dependency group to be installed.
    """
    try:
        import markdown
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on install
        raise RuntimeError(
            "building the site needs the docs dependencies: uv sync --group docs"
        ) from exc

    # "tables" for the odd Markdown table, "toc" for heading anchors so entries
    # are directly linkable, "sane_lists" to keep list parsing predictable.
    return markdown.markdown(text, extensions=["tables", "toc", "sane_lists"])


def _strip_leading_h1(fragment: str) -> str:
    """Drop the document's own ``<h1>``.

    The page template supplies the heading, and two ``<h1>`` elements on one page
    is both redundant and a screen-reader annoyance.
    """
    return re.sub(r"^\s*<h1[^>]*>.*?</h1>\s*", "", fragment, count=1, flags=re.S)


def _page(*, title: str, description: str, body: str, depth: int = 0) -> str:
    """Wrap a body fragment in the shared HTML shell.

    ``depth`` is how many directories deep the page sits, so relative asset
    links stay correct. Everything is served from a subpath on
    ``*.github.io``, so absolute paths would break.
    """
    prefix = "../" * depth
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<link rel="stylesheet" href="{prefix}style.css">
</head>
<body>
<a class="skip-link" href="#content">Skip to content</a>
<header class="masthead">
<a class="wordmark" href="{prefix}index.html">{html.escape(SITE_TITLE)}</a>
</header>
<main id="content">
{body}
</main>
<footer class="site-footer">
<p>Generated by
<a href="https://github.com/woojar/technical-trend">technical-trend</a>.
Every summary links to its original source.</p>
</footer>
</body>
</html>
"""


def render_issue_page(issue: Issue) -> str:
    """Full HTML for one issue."""
    fragment = _strip_leading_h1(_markdown_to_html(issue.markdown))

    meta_bits = []
    if issue.date_range:
        meta_bits.append(html.escape(issue.date_range))
    if issue.story_count:
        meta_bits.append(f"{issue.story_count} stories")
    meta = " · ".join(meta_bits)

    notice = ""
    if issue.degraded:
        # Say it on the page too, not just in the Markdown footer, so a plainer
        # issue is never mistaken for a normal one.
        notice = (
            '<p class="notice" role="note">No language model was reachable when this '
            "issue was built, so entries show upstream titles and excerpts rather than "
            "written summaries.</p>"
        )

    body = f"""<article class="issue">
<h1>{html.escape(issue.title)}</h1>
{f'<p class="meta">{meta}</p>' if meta else ""}
{notice}
{fragment}
</article>
<nav class="pager" aria-label="Archive"><a href="index.html">All issues</a></nav>
"""
    description = f"{SITE_TITLE} for {issue.week}"
    if issue.date_range:
        description += f", covering {issue.date_range}"
    return _page(title=issue.title, description=description, body=body)


def _index_row(issue: Issue) -> str:
    """One archive entry. Built imperatively; a nested f-string for this was
    unreadable and relied on quoting rules only valid from Python 3.12."""
    parts = [f'<li><a href="{html.escape(issue.output_name)}">{html.escape(issue.week)}</a>']

    if issue.date_range:
        meta = html.escape(issue.date_range)
        if issue.story_count:
            meta += f" · {issue.story_count} stories"
        parts.append(f' <span class="meta">{meta}</span>')

    if issue.degraded:
        parts.append(' <span class="tag">plain</span>')

    parts.append("</li>")
    return "".join(parts)


def render_index_page(issues: list[Issue]) -> str:
    """Archive listing, newest first."""
    if issues:
        rows = "\n".join(_index_row(i) for i in issues)
        listing = f'<ul class="issue-list">\n{rows}\n</ul>'
        newest = issues[0]
        latest = (
            '<p class="lede">Latest issue: '
            f'<a href="{html.escape(newest.output_name)}">{html.escape(newest.week)}</a></p>'
        )
    else:
        listing = '<p class="empty">No issues published yet.</p>'
        latest = ""

    body = f"""<h1>{html.escape(SITE_TITLE)}</h1>
<p class="tagline">{html.escape(SITE_DESCRIPTION)}</p>
{latest}
<h2>Archive</h2>
{listing}
"""
    return _page(title=SITE_TITLE, description=SITE_DESCRIPTION, body=body)


STYLESHEET = """\
/* Deliberately small and dependency-free: a reading page needs type, measure
   and contrast, not a framework. */
:root {
  --fg: #1a1a1a;
  --muted: #5a5f66;
  --bg: #ffffff;
  --accent: #0b5cad;   /* 5.4:1 on white, above the 4.5:1 minimum */
  --rule: #e2e5e9;
  --notice-bg: #fff6e0;
  --notice-fg: #6b4a00;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fg: #e8e8e8;
    --muted: #a2a9b2;
    --bg: #14171a;
    --accent: #7fb6ef;  /* 6.9:1 on the dark background */
    --rule: #2c3238;
    --notice-bg: #33291a;
    --notice-fg: #f0d9a8;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font: 17px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        "Helvetica Neue", Arial, sans-serif;
}
main { max-width: 46rem; margin: 0 auto; padding: 0 1.25rem 4rem; }
.masthead {
  border-bottom: 1px solid var(--rule);
  margin-bottom: 2rem;
  padding: 1rem 1.25rem;
}
.wordmark {
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--fg);
  text-decoration: none;
}
a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }
a:hover { text-decoration-thickness: 2px; }
/* Never remove focus rings: keyboard users need them. */
a:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.skip-link {
  position: absolute;
  left: -9999px;
  background: var(--bg);
  color: var(--fg);
  padding: 0.6rem 1rem;
  z-index: 10;
}
.skip-link:focus {
  left: 1rem;
  top: 1rem;
  border: 2px solid var(--accent);
  border-radius: 4px;
}
h1 { font-size: 1.9rem; line-height: 1.25; letter-spacing: -0.02em; margin: 0 0 0.4rem; }
h2 {
  font-size: 1.3rem;
  margin: 2.75rem 0 1rem;
  padding-bottom: 0.35rem;
  border-bottom: 1px solid var(--rule);
}
h3 { font-size: 1.06rem; line-height: 1.4; margin: 2rem 0 0.4rem; }
.tagline, .lede { color: var(--muted); }
.meta { color: var(--muted); font-size: 0.9rem; }
/* The Markdown renderer emits the per-entry byline as emphasis. */
article em { color: var(--muted); font-style: normal; font-size: 0.875rem; }
.notice {
  background: var(--notice-bg);
  color: var(--notice-fg);
  border-radius: 6px;
  padding: 0.75rem 1rem;
  font-size: 0.925rem;
}
.issue-list { list-style: none; padding: 0; }
.issue-list li { border-bottom: 1px solid var(--rule); padding: 0.7rem 0; }
.issue-list a { font-weight: 600; }
.tag {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  border: 1px solid var(--rule);
  border-radius: 999px;
  padding: 0.1rem 0.45rem;
  color: var(--muted);
}
.pager { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--rule); }
.site-footer {
  max-width: 46rem;
  margin: 0 auto;
  padding: 1.5rem 1.25rem 3rem;
  color: var(--muted);
  font-size: 0.875rem;
  border-top: 1px solid var(--rule);
}
hr { border: 0; border-top: 1px solid var(--rule); margin: 2.5rem 0 1.5rem; }
"""


def build_site(issues: list[Issue], out_dir: Path) -> list[Path]:
    """Write the full site to ``out_dir`` and return the files created.

    Takes already-discovered issues so one build reads the digest directory once.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []

    style_path = out_dir / "style.css"
    style_path.write_text(STYLESHEET, encoding="utf-8")
    written.append(style_path)

    index_path = out_dir / "index.html"
    index_path.write_text(render_index_page(issues), encoding="utf-8")
    written.append(index_path)

    for issue in issues:
        page = out_dir / issue.output_name
        page.write_text(render_issue_page(issue), encoding="utf-8")
        written.append(page)

    # Jekyll would otherwise ignore files and directories beginning with an
    # underscore. Cheap insurance against a confusing partial deploy.
    nojekyll = out_dir / ".nojekyll"
    nojekyll.write_text("", encoding="utf-8")
    written.append(nojekyll)

    log.info("site: wrote %d file(s) to %s", len(written), out_dir)
    return written


def build_feed(issues: list[Issue], out_dir: Path, base_url: str) -> Path | None:
    """Write an RSS feed so readers can subscribe rather than remember to visit.

    Returns ``None`` when there is nothing to publish or no ``base_url``, since
    feed entries require absolute links to be useful.
    """
    if not base_url or not issues:
        return None

    root = base_url.rstrip("/")
    now = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")

    items = []
    for issue in issues:
        link = f"{root}/{issue.output_name}"
        summary = f"{issue.story_count} stories" if issue.story_count else "New issue"
        if issue.date_range:
            summary += f", {issue.date_range}"
        items.append(
            "<item>"
            f"<title>{html.escape(issue.title)}</title>"
            f"<link>{html.escape(link)}</link>"
            f'<guid isPermaLink="true">{html.escape(link)}</guid>'
            f"<description>{html.escape(summary)}</description>"
            "</item>"
        )

    feed = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        f"<title>{html.escape(SITE_TITLE)}</title>"
        f"<link>{html.escape(root)}/</link>"
        f"<description>{html.escape(SITE_DESCRIPTION)}</description>"
        f"<lastBuildDate>{now}</lastBuildDate>" + "".join(items) + "</channel></rss>\n"
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "feed.xml"
    path.write_text(feed, encoding="utf-8")
    return path
