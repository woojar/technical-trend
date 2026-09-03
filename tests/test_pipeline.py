"""Pipeline orchestration, end to end with fake sources and no network."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import FakeProvider, make_item
from trend.config import load_config
from trend.models import Item
from trend.pipeline import build_digest, fetch_items, iso_week, write_digest
from trend.store import Store

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

CONFIG_YAML = """
window_days: 7
max_entries: 10
max_per_category: 2
skip_seen: true
output_dir: digests
state_db: state.db
categories:
  - Infrastructure & Cloud
  - Security
  - Industry & Community
llm:
  batch_size: 5
  chain:
    - provider: gemini
      model: test-model
      api_key_env: TREND_FAKE_KEY
sources:
  hackernews:
    enabled: true
  github:
    enabled: false
"""


class FakeSource:
    name = "hackernews"
    weight = 1.0

    def __init__(self, items: list[Item]) -> None:
        self.items = items
        self.calls = 0

    def fetch(self, ctx) -> list[Item]:
        self.calls += 1
        return list(self.items)


class BrokenSource:
    name = "hackernews"
    weight = 1.0

    def fetch(self, ctx) -> list[Item]:
        raise RuntimeError("upstream is down")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    return tmp_path


def _items() -> list[Item]:
    return [
        make_item(
            "Kubernetes 1.35 improves scheduling",
            "https://k8s.example/1",
            points=500,
            excerpt="Scheduler changes.",
        ),
        make_item(
            "Kubernetes 1.35 improves scheduling",
            "https://k8s.example/1?utm_source=x",
            source="rss:Feed",
        ),
        make_item("Critical CVE in OpenSSL", "https://sec.example/2", points=400),
        make_item("A minor CLI utility", "https://cli.example/3", points=120),
    ]


def _install(monkeypatch, source) -> None:
    monkeypatch.setattr("trend.pipeline.build_source", lambda name: source)


def _router_reply(indexes: list[int], category: str = "Infrastructure & Cloud") -> str:
    return json.dumps(
        {
            "items": [
                {
                    "index": i,
                    "category": category,
                    "headline": f"Headline {i}",
                    "summary": f"Summary {i}.",
                    "why_it_matters": f"Matters {i}.",
                }
                for i in indexes
            ]
        }
    )


def test_iso_week_label() -> None:
    assert iso_week(datetime(2026, 9, 3, tzinfo=UTC)) == "2026-W36"
    assert iso_week(datetime(2026, 1, 5, tzinfo=UTC)) == "2026-W02"


def test_fetch_only_runs_enabled_sources(project, monkeypatch) -> None:
    source = FakeSource(_items())
    _install(monkeypatch, source)
    cfg = load_config("config.yaml", root=project)

    result = fetch_items(cfg, now=NOW)
    # github is disabled in the config, so exactly one source ran.
    assert source.calls == 1
    assert result.per_source == {"hackernews": 4}
    assert len(result.items) == 4


def test_failing_source_does_not_abort_the_run(project, monkeypatch) -> None:
    _install(monkeypatch, BrokenSource())
    cfg = load_config("config.yaml", root=project)
    result = fetch_items(cfg, now=NOW)
    assert result.items == []


def test_build_digest_produces_grouped_entries(project, monkeypatch) -> None:
    _install(monkeypatch, FakeSource(_items()))
    monkeypatch.setattr(
        "trend.pipeline.Router.from_config",
        classmethod(
            lambda cls, chain, session=None: cls(
                [FakeProvider("fake", [_router_reply([0, 1, 2]), "Intro paragraph."])]
            )
        ),
    )
    cfg = load_config("config.yaml", root=project)
    digest = build_digest(cfg, now=NOW)

    assert digest.week == "2026-W36"
    assert digest.provider == "fake"
    assert digest.intro == "Intro paragraph."
    # Four items, two of which are the same story, so three unique stories.
    assert digest.stats["fetched"] == 4
    assert digest.stats["clustered"] == 3


def test_per_category_cap_is_enforced(project, monkeypatch) -> None:
    """One hot topic must not swamp an entire issue."""
    _install(monkeypatch, FakeSource(_items()))
    monkeypatch.setattr(
        "trend.pipeline.Router.from_config",
        classmethod(
            lambda cls, chain, session=None: cls(
                [FakeProvider("fake", [_router_reply([0, 1, 2]), "Intro."])]
            )
        ),
    )
    cfg = load_config("config.yaml", root=project)
    digest = build_digest(cfg, now=NOW)

    # All three were labelled Infrastructure, but max_per_category is 2.
    assert len(digest.entries) == 2
    assert all(e.category == "Infrastructure & Cloud" for e in digest.entries)


def test_digest_builds_without_any_llm(project, monkeypatch) -> None:
    """The core reliability guarantee: output even with zero LLM access."""
    _install(monkeypatch, FakeSource(_items()))
    monkeypatch.setattr(
        "trend.pipeline.Router.from_config",
        classmethod(lambda cls, chain, session=None: cls([])),
    )
    cfg = load_config("config.yaml", root=project)
    digest = build_digest(cfg, now=NOW)

    assert digest.provider == ""
    assert digest.intro == ""
    assert len(digest.entries) >= 1
    # Headlines are the upstream titles, not model text.
    assert any("Kubernetes" in e.title for e in digest.entries)


def test_seen_items_are_suppressed_on_the_next_run(project, monkeypatch) -> None:
    _install(monkeypatch, FakeSource(_items()))
    monkeypatch.setattr(
        "trend.pipeline.Router.from_config",
        classmethod(lambda cls, chain, session=None: cls([])),
    )
    cfg = load_config("config.yaml", root=project)

    first = build_digest(cfg, now=NOW)
    assert first.entries
    assert Store(cfg.state_db).count() > 0

    second = build_digest(cfg, now=NOW)
    assert second.entries == []


def test_dry_run_does_not_create_state(project, monkeypatch) -> None:
    _install(monkeypatch, FakeSource(_items()))
    monkeypatch.setattr(
        "trend.pipeline.Router.from_config",
        classmethod(lambda cls, chain, session=None: cls([])),
    )
    cfg = load_config("config.yaml", root=project)

    build_digest(cfg, now=NOW, dry_run=True)
    assert not cfg.state_db.exists()


def test_dry_run_previews_what_a_real_run_would_publish(project, monkeypatch) -> None:
    """A preview that ignored published state would be misleading."""
    _install(monkeypatch, FakeSource(_items()))
    monkeypatch.setattr(
        "trend.pipeline.Router.from_config",
        classmethod(lambda cls, chain, session=None: cls([])),
    )
    cfg = load_config("config.yaml", root=project)

    build_digest(cfg, now=NOW)  # publishes and records
    recorded = Store(cfg.state_db).count()

    preview = build_digest(cfg, now=NOW, dry_run=True)
    assert preview.entries == []
    # Reading state must not mutate it.
    assert Store(cfg.state_db).count() == recorded


def test_write_digest_creates_week_file_and_latest(project, monkeypatch) -> None:
    _install(monkeypatch, FakeSource(_items()))
    monkeypatch.setattr(
        "trend.pipeline.Router.from_config",
        classmethod(lambda cls, chain, session=None: cls([])),
    )
    cfg = load_config("config.yaml", root=project)
    digest = build_digest(cfg, now=NOW)
    path = write_digest(digest, cfg)

    assert path == project / "digests" / "2026-W36.md"
    assert path.is_file()
    content = path.read_text(encoding="utf-8")
    assert "# Tech Trends — 2026-W36" in content
    # latest.md is a stable path for linking from the README.
    assert (project / "digests" / "latest.md").read_text(encoding="utf-8") == content


def test_empty_source_yields_empty_digest(project, monkeypatch) -> None:
    _install(monkeypatch, FakeSource([]))
    cfg = load_config("config.yaml", root=project)
    digest = build_digest(cfg, now=NOW)
    assert digest.entries == []
