"""Config loading, dotenv handling, and the seen-item store."""

from __future__ import annotations

from pathlib import Path

from trend.config import DEFAULT_CATEGORIES, Config, load_config, load_dotenv
from trend.store import Store

CONFIG_YAML = """
window_days: 14
max_entries: 10
max_per_category: 2
dedupe_threshold: 0.8
skip_seen: false
output_dir: out
state_db: db/seen.db
categories:
  - Alpha
  - Beta
llm:
  batch_size: 4
  chain:
    - provider: gemini
      model: gemini-flash-latest
      api_key_env: GEMINI_API_KEY
sources:
  hackernews:
    enabled: true
    min_points: 250
  github:
    enabled: false
"""


def test_defaults_when_config_missing(tmp_path: Path) -> None:
    cfg = load_config("nonexistent.yaml", root=tmp_path)
    assert cfg.window_days == 7
    assert cfg.categories == DEFAULT_CATEGORIES
    # The built-in free-provider chain is used when none is configured.
    first = cfg.llm_chain[0]
    assert first.get("name", first["provider"]) == "gemini"
    assert cfg.batch_size == 12


def test_values_are_read_from_yaml(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    cfg = load_config("config.yaml", root=tmp_path)

    assert cfg.window_days == 14
    assert cfg.max_entries == 10
    assert cfg.max_per_category == 2
    assert cfg.dedupe_threshold == 0.8
    assert cfg.skip_seen is False
    assert cfg.categories == ["Alpha", "Beta"]
    assert cfg.batch_size == 4
    assert len(cfg.llm_chain) == 1


def test_relative_paths_resolve_against_root(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    cfg = load_config("config.yaml", root=tmp_path)
    assert cfg.output_dir == tmp_path / "out"
    assert cfg.state_db == tmp_path / "db" / "seen.db"


def test_absolute_paths_are_preserved(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(f"output_dir: {tmp_path / 'abs'}\n", encoding="utf-8")
    cfg = load_config("config.yaml", root=tmp_path)
    assert cfg.output_dir == tmp_path / "abs"


def test_source_enabled_and_options(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    cfg = load_config("config.yaml", root=tmp_path)

    assert cfg.source_enabled("hackernews") is True
    assert cfg.source_enabled("github") is False
    assert cfg.source_enabled("absent") is False
    assert cfg.source_cfg("hackernews")["min_points"] == 250
    assert cfg.source_cfg("absent") == {}


def test_malformed_yaml_falls_back_to_defaults(tmp_path: Path) -> None:
    """A scalar or list at the top level must not crash the run."""
    (tmp_path / "config.yaml").write_text("just a string\n", encoding="utf-8")
    assert load_config("config.yaml", root=tmp_path).window_days == 7


# --- dotenv ----------------------------------------------------------------


def test_dotenv_sets_missing_variables(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TREND_TEST_KEY", raising=False)
    (tmp_path / ".env").write_text('TREND_TEST_KEY="from-file"\n', encoding="utf-8")
    load_dotenv(tmp_path / ".env")
    import os

    assert os.environ["TREND_TEST_KEY"] == "from-file"


def test_dotenv_does_not_override_real_environment(tmp_path: Path, monkeypatch) -> None:
    """CI secrets must win over a stale local .env."""
    monkeypatch.setenv("TREND_TEST_KEY2", "from-env")
    (tmp_path / ".env").write_text("TREND_TEST_KEY2=from-file\n", encoding="utf-8")
    load_dotenv(tmp_path / ".env")
    import os

    assert os.environ["TREND_TEST_KEY2"] == "from-env"


def test_dotenv_ignores_comments_and_blanks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TREND_TEST_KEY3", raising=False)
    (tmp_path / ".env").write_text(
        "\n# a comment\n\nTREND_TEST_KEY3='quoted'\nnot_a_pair\n", encoding="utf-8"
    )
    load_dotenv(tmp_path / ".env")
    import os

    assert os.environ["TREND_TEST_KEY3"] == "quoted"


def test_dotenv_missing_file_is_noop(tmp_path: Path) -> None:
    load_dotenv(tmp_path / "nope.env")  # must not raise


# --- store -----------------------------------------------------------------


def test_store_roundtrip(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    assert store.seen_urls() == set()

    store.mark_seen([("https://a.example/1", "One")], "2026-W36")
    assert store.seen_urls() == {"https://a.example/1"}
    assert store.count() == 1


def test_store_ignores_duplicate_urls(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    store.mark_seen([("https://a.example/1", "One")], "2026-W36")
    store.mark_seen([("https://a.example/1", "One again")], "2026-W37")
    assert store.count() == 1


def test_store_skips_empty_urls(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    assert store.mark_seen([("", "No URL")], "2026-W36") == 0
    assert store.count() == 0


def test_store_creates_parent_directories(tmp_path: Path) -> None:
    store = Store(tmp_path / "nested" / "dir" / "state.db")
    store.mark_seen([("https://a.example/1", "One")], "2026-W36")
    assert (tmp_path / "nested" / "dir" / "state.db").is_file()


def test_store_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    Store(path).mark_seen([("https://a.example/1", "One")], "2026-W36")
    assert Store(path).seen_urls() == {"https://a.example/1"}


def test_config_dataclass_defaults_are_independent() -> None:
    """Mutable defaults must not be shared between instances."""
    a, b = Config(), Config()
    a.categories.append("Mutated")
    assert "Mutated" not in b.categories
