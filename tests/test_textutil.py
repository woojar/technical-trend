"""HTML-to-text extraction for upstream excerpt fields."""

from __future__ import annotations

from trend.textutil import excerpt, html_to_text


def test_strips_tags_and_entities() -> None:
    raw = "<p>Hello&nbsp;&amp; welcome</p><script>alert('x')</script><style>p{}</style>"
    assert html_to_text(raw) == "Hello & welcome"


def test_collapses_whitespace() -> None:
    assert html_to_text("<p>a</p>\n\n   <p>b</p>") == "a b"


def test_no_space_left_before_punctuation() -> None:
    """Removing an inline tag mid-sentence must not leave 'routing .'."""
    assert html_to_text("<p>We rebuilt <b>routing</b>.</p>") == "We rebuilt routing."


def test_hacker_news_story_text_is_readable() -> None:
    """Regression: this shape reached a rendered digest verbatim.

    Hacker News sends live anchor tags whose attributes are entity-escaped, so
    unescaping has to happen before tags are stripped.
    """
    raw = (
        "What&#x27;s new in Claude 5.1 &#x2013; <a "
        'href="https:&#x2F;&#x2F;platform.claude.com&#x2F;docs" rel="nofollow">'
        "https:&#x2F;&#x2F;platform.claude.com&#x2F;docs</a><p>Some more text."
    )
    result = html_to_text(raw)
    assert "<a" not in result
    assert "&#x27;" not in result
    assert "&#x2F;" not in result
    assert result.startswith("What's new in Claude 5.1")
    assert "Some more text." in result


def test_double_escaped_fragment_is_handled() -> None:
    assert html_to_text("&lt;p&gt;Escaped &lt;b&gt;markup&lt;/b&gt;&lt;/p&gt;") == (
        "Escaped markup"
    )


def test_empty_input() -> None:
    assert html_to_text("") == ""
    assert excerpt("", 100) == ""


def test_excerpt_leaves_short_text_untouched() -> None:
    assert excerpt("<p>Short enough.</p>", 100) == "Short enough."


def test_excerpt_truncates_on_word_boundary() -> None:
    text = " ".join(["alpha"] * 100)
    result = excerpt(text, 40)
    assert len(result) <= 40
    assert result.endswith("...")
    # Truncation must not split a word.
    assert "alph." not in result
    assert result.removesuffix("...").split()[-1] == "alpha"


def test_excerpt_never_exceeds_the_limit() -> None:
    """The ellipsis counts against the limit rather than overflowing it."""
    for limit in (10, 40, 280):
        assert len(excerpt(" ".join(["word"] * 200), limit)) <= limit


def test_excerpt_limit_counts_readable_characters_not_markup() -> None:
    """Converting before truncating is what keeps the limit meaningful."""
    raw = '<a href="https://example.com/a/very/long/tracking/url">Hi</a> there friend'
    assert excerpt(raw, 40) == "Hi there friend"
