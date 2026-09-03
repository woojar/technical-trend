"""Static site generation. Reads committed Markdown, writes HTML; no network."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import pytest

from trend.site import (
    build_feed,
    build_site,
    discover_issues,
    render_index_page,
    render_issue_page,
)

#: Markdown conversion lives in the optional docs group, so tests that render
#: HTML skip rather than fail for anyone running only the core dependencies.
#: Discovery, metadata parsing and feed generation need no converter, so those
#: tests always run.
try:  # pragma: no cover - depends on which dependency groups are installed
    import markdown  # noqa: F401

    HAS_MARKDOWN = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_MARKDOWN = False

needs_markdown = pytest.mark.skipif(
    not HAS_MARKDOWN, reason="needs the docs group: uv sync --group docs"
)

DIGEST = """\
# Tech Trends — 2026-W36

*2026-08-27 to 2026-09-03 · 24 stories*

An opening paragraph about the week.

## Contents

- [Security](#security) (1)

## Security

### 1. [A patched CVE](https://example.com/cve)

*Hacker News · 900 points · 2026-09-01*

Something was fixed.

**Why it matters:** Upgrade sooner rather than later.

---

*Generated 2026-09-03 03:05 UTC · 335 items fetched · summarized by gemini.*
"""

DEGRADED_DIGEST = DIGEST.replace(
    "summarized by gemini", "no LLM available — headlines and excerpts only"
)


@pytest.fixture
def digests(tmp_path: Path) -> Path:
    d = tmp_path / "digests"
    d.mkdir()
    (d / "2026-W36.md").write_text(DIGEST, encoding="utf-8")
    # latest.md duplicates the newest issue and must not become a second page.
    (d / "latest.md").write_text(DIGEST, encoding="utf-8")
    return d


# --- discovery -------------------------------------------------------------


def test_discovers_issue_and_parses_metadata(digests: Path) -> None:
    issues = discover_issues(digests)
    assert len(issues) == 1

    issue = issues[0]
    assert issue.week == "2026-W36"
    assert issue.start == "2026-08-27"
    assert issue.end == "2026-09-03"
    assert issue.story_count == 24
    assert issue.degraded is False
    assert issue.output_name == "2026-W36.html"


def test_latest_md_is_excluded(digests: Path) -> None:
    """It is a copy of the newest issue; including it would duplicate the page."""
    assert [i.week for i in discover_issues(digests)] == ["2026-W36"]


def test_issues_are_ordered_newest_first(tmp_path: Path) -> None:
    d = tmp_path / "digests"
    d.mkdir()
    for week in ("2025-W02", "2026-W36", "2026-W02", "2025-W51"):
        (d / f"{week}.md").write_text(DIGEST, encoding="utf-8")

    assert [i.week for i in discover_issues(d)] == [
        "2026-W36",
        "2026-W02",
        "2025-W51",
        "2025-W02",
    ]


def test_degraded_issue_is_flagged(tmp_path: Path) -> None:
    d = tmp_path / "digests"
    d.mkdir()
    (d / "2026-W36.md").write_text(DEGRADED_DIGEST, encoding="utf-8")
    assert discover_issues(d)[0].degraded is True


def test_missing_metadata_does_not_break_discovery(tmp_path: Path) -> None:
    """An issue predating the current renderer must still publish."""
    d = tmp_path / "digests"
    d.mkdir()
    (d / "2026-W01.md").write_text("# Old issue\n\nNo subtitle line.\n", encoding="utf-8")

    issue = discover_issues(d)[0]
    assert issue.week == "2026-W01"
    assert issue.story_count == 0
    assert issue.date_range == ""


def test_missing_directory_returns_nothing(tmp_path: Path) -> None:
    assert discover_issues(tmp_path / "absent") == []


# --- page rendering --------------------------------------------------------


@needs_markdown
def test_issue_page_contains_converted_markdown(digests: Path) -> None:
    html_out = render_issue_page(discover_issues(digests)[0])
    assert '<a href="https://example.com/cve">' in html_out
    assert "Something was fixed." in html_out
    assert "<h2" in html_out


@needs_markdown
def test_issue_page_has_exactly_one_h1(digests: Path) -> None:
    """The template supplies the heading, so the Markdown's own h1 is dropped."""
    html_out = render_issue_page(discover_issues(digests)[0])
    assert html_out.count("<h1>") == 1


@needs_markdown
def test_issue_page_shows_metadata(digests: Path) -> None:
    html_out = render_issue_page(discover_issues(digests)[0])
    assert "2026-08-27 to 2026-09-03" in html_out
    assert "24 stories" in html_out


@needs_markdown
def test_degraded_issue_says_so_on_the_page(tmp_path: Path) -> None:
    d = tmp_path / "digests"
    d.mkdir()
    (d / "2026-W36.md").write_text(DEGRADED_DIGEST, encoding="utf-8")

    html_out = render_issue_page(discover_issues(d)[0])
    assert 'role="note"' in html_out
    assert "No language model was reachable" in html_out


@needs_markdown
def test_normal_issue_has_no_degraded_notice(digests: Path) -> None:
    assert "No language model was reachable" not in render_issue_page(discover_issues(digests)[0])


def test_index_lists_issues_and_highlights_newest(tmp_path: Path) -> None:
    d = tmp_path / "digests"
    d.mkdir()
    for week in ("2026-W35", "2026-W36"):
        (d / f"{week}.md").write_text(DIGEST, encoding="utf-8")

    html_out = render_index_page(discover_issues(d))
    assert 'href="2026-W36.html"' in html_out
    assert 'href="2026-W35.html"' in html_out
    assert html_out.index("2026-W36.html") < html_out.index("2026-W35.html")
    assert "Latest issue" in html_out


def test_index_handles_no_issues() -> None:
    html_out = render_index_page([])
    assert "No issues published yet" in html_out
    assert "Latest issue" not in html_out


def test_titles_are_html_escaped(tmp_path: Path) -> None:
    """A week label comes from a filename, so treat it as untrusted input."""
    d = tmp_path / "digests"
    d.mkdir()
    # No slash: that would be a path separator rather than a filename.
    (d / '2026-W36"><img src=x onerror=alert(1)>.md').write_text(DIGEST, encoding="utf-8")

    html_out = render_index_page(discover_issues(d))
    assert "<img src=x onerror=alert(1)>" not in html_out
    assert "&lt;img" in html_out
    assert "&quot;&gt;" in html_out


# --- accessibility and structure ------------------------------------------

_VOID = frozenset({"meta", "link", "br", "img", "hr", "input", "area", "base", "col"})


class _Balance(HTMLParser):
    """Minimal well-formedness check on generated markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.mismatched: list[str] = []

    def handle_startendtag(self, tag: str, attrs) -> None:
        return  # <hr /> needs no closing tag

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.mismatched.append(tag)


@pytest.mark.parametrize("page", ["index.html", "2026-W36.html"])
@needs_markdown
def test_generated_pages_are_well_formed(digests: Path, tmp_path: Path, page: str) -> None:
    build_site(discover_issues(digests), tmp_path / "site")
    checker = _Balance()
    checker.feed((tmp_path / "site" / page).read_text(encoding="utf-8"))
    assert checker.stack == []
    assert checker.mismatched == []


@pytest.mark.parametrize("page", ["index.html", "2026-W36.html"])
@needs_markdown
def test_generated_pages_meet_accessibility_basics(
    digests: Path, tmp_path: Path, page: str
) -> None:
    build_site(discover_issues(digests), tmp_path / "site")
    html_out = (tmp_path / "site" / page).read_text(encoding="utf-8")

    assert html_out.lstrip().startswith("<!DOCTYPE html>")
    assert '<html lang="en">' in html_out  # screen readers need the language
    assert 'name="viewport"' in html_out  # usable when zoomed or on a phone
    assert 'class="skip-link"' in html_out  # keyboard bypass of the header
    assert '<main id="content">' in html_out  # landmark the skip link targets
    assert "<title>" in html_out


@needs_markdown
def test_stylesheet_keeps_visible_focus_rings(digests: Path, tmp_path: Path) -> None:
    """Removing focus outlines would strand keyboard users."""
    build_site(discover_issues(digests), tmp_path / "site")
    css = (tmp_path / "site" / "style.css").read_text(encoding="utf-8")
    assert "focus-visible" in css
    assert "outline: none" not in css


# --- build_site ------------------------------------------------------------


@needs_markdown
def test_build_site_writes_expected_files(digests: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    written = build_site(discover_issues(digests), out)

    names = {p.name for p in written}
    assert names == {"index.html", "2026-W36.html", "style.css", ".nojekyll"}
    assert all(p.is_file() for p in written)


@needs_markdown
def test_nojekyll_is_written(digests: Path, tmp_path: Path) -> None:
    """Without it, Pages would drop any path beginning with an underscore."""
    out = tmp_path / "site"
    build_site(discover_issues(digests), out)
    assert (out / ".nojekyll").is_file()


@needs_markdown
def test_build_site_is_idempotent(digests: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    issues = discover_issues(digests)
    first = (out / "index.html") if build_site(issues, out) else None
    assert first is not None
    before = first.read_text(encoding="utf-8")
    build_site(issues, out)
    assert first.read_text(encoding="utf-8") == before


@needs_markdown
def test_build_site_with_no_issues_still_writes_an_index(tmp_path: Path) -> None:
    out = tmp_path / "site"
    build_site([], out)
    assert "No issues published yet" in (out / "index.html").read_text(encoding="utf-8")


# --- feed ------------------------------------------------------------------


def test_feed_is_valid_xml_with_absolute_links(digests: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    path = build_feed(discover_issues(digests), out, "https://woojar.github.io/technical-trend")
    assert path is not None

    root = ET.parse(path).getroot()
    items = root.findall(".//item")
    assert len(items) == 1
    assert items[0].findtext("link") == "https://woojar.github.io/technical-trend/2026-W36.html"


def test_feed_tolerates_a_trailing_slash_in_base_url(digests: Path, tmp_path: Path) -> None:
    path = build_feed(discover_issues(digests), tmp_path / "site", "https://example.com/site/")
    assert path is not None
    assert "https://example.com/site/2026-W36.html" in path.read_text(encoding="utf-8")


def test_feed_skipped_without_base_url(digests: Path, tmp_path: Path) -> None:
    """Feed links must be absolute, so there is nothing useful to emit."""
    assert build_feed(discover_issues(digests), tmp_path / "site", "") is None


def test_feed_skipped_without_issues(tmp_path: Path) -> None:
    assert build_feed([], tmp_path / "site", "https://example.com") is None
