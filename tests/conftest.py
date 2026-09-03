"""Shared fixtures. No test in this suite touches the network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trend.llm.base import Completion, LLMError
from trend.models import Item

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    return NOW


def make_item(
    title: str,
    url: str,
    *,
    source: str = "hackernews",
    points: int = 0,
    comments: int = 0,
    age_days: float = 1.0,
    excerpt: str = "",
    extra: dict | None = None,
) -> Item:
    return Item(
        title=title,
        url=url,
        source=source,
        published=NOW - timedelta(days=age_days),
        points=points,
        comments=comments,
        excerpt=excerpt,
        extra=extra or {},
    )


class FakeProvider:
    """Scripted provider for router tests.

    ``responses`` entries are either text to return or an exception to raise.
    """

    def __init__(self, name: str, responses: list, *, configured: bool = True) -> None:
        self.name = name
        self.model = f"{name}-model"
        self.responses = list(responses)
        self.configured = configured
        self.calls = 0

    def available(self) -> bool:
        return self.configured

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> Completion:
        self.calls += 1
        if not self.responses:
            raise LLMError(f"{self.name}: exhausted")
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return Completion(text=outcome, provider=self.name, model=self.model)
