"""Configuration loading: ``config.yaml`` plus environment overrides.

Secrets never live in the config file -- they are read from the environment (and
from a local ``.env`` if present) so the config can be committed safely.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CATEGORIES = [
    "AI & Machine Learning",
    "Languages & Runtimes",
    "Infrastructure & Cloud",
    "Developer Tools",
    "Security",
    "Research",
    "Industry & Community",
]

# Free-tier-friendly defaults. Each entry names a provider implementation, the
# model to ask for, and the environment variable holding the key.
#
# A dated Gemini slug leads and the "-latest" alias backs it up. The alias never
# goes stale, but it resolves to the newest model, whose free-tier quota is
# heavily contended (measured at one request before HTTP 429), so it makes a poor
# primary and a good safety net for the day the dated slug is retired.
DEFAULT_LLM_CHAIN: list[dict[str, Any]] = [
    {
        "provider": "gemini",
        "model": "gemini-3.6-flash",
        "api_key_env": "GEMINI_API_KEY",
    },
    {
        "provider": "gemini",
        "name": "gemini-latest",
        "model": "gemini-flash-latest",
        "api_key_env": "GEMINI_API_KEY",
    },
    {
        "provider": "openai_compat",
        "name": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
    },
    {
        "provider": "openai_compat",
        "name": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    {
        "provider": "openai_compat",
        "name": "cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "model": "llama-3.3-70b",
        "api_key_env": "CEREBRAS_API_KEY",
    },
    {
        "provider": "openai_compat",
        "name": "ollama",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:7b-instruct",
        "api_key_env": "",  # local, no key required
    },
]


def load_dotenv(path: Path) -> None:
    """Minimal ``.env`` loader.

    Existing environment variables always win, so CI secrets are never shadowed
    by a stale local file. Avoids a python-dotenv dependency for ~15 lines.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(slots=True)
class Config:
    window_days: int = 7
    max_entries: int = 30
    max_per_category: int = 6
    dedupe_threshold: float = 0.6
    categories: list[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))
    llm: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, Any] = field(default_factory=dict)
    output_dir: Path = Path("digests")
    state_db: Path = Path("state.db")
    #: Skip items already published in an earlier digest.
    skip_seen: bool = True

    @property
    def llm_chain(self) -> list[dict[str, Any]]:
        chain = self.llm.get("chain")
        return chain if chain else list(DEFAULT_LLM_CHAIN)

    @property
    def batch_size(self) -> int:
        """Items per LLM request.

        Batching is what keeps this inside free-tier request-per-day limits: a
        30-item digest costs 2 calls, not 30.
        """
        return int(self.llm.get("batch_size", 12))

    def source_cfg(self, name: str) -> dict[str, Any]:
        cfg = self.sources.get(name) or {}
        return cfg if isinstance(cfg, dict) else {}

    def source_enabled(self, name: str) -> bool:
        return bool(self.source_cfg(name).get("enabled", False))


def load_config(path: str | Path = "config.yaml", *, root: Path | None = None) -> Config:
    """Read ``config.yaml``, falling back to defaults for anything absent."""
    root = root or Path.cwd()
    load_dotenv(root / ".env")

    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = root / config_path

    data: dict[str, Any] = {}
    if config_path.is_file():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded

    output_dir = Path(data.get("output_dir", "digests"))
    state_db = Path(data.get("state_db", "state.db"))

    return Config(
        window_days=int(data.get("window_days", 7)),
        max_entries=int(data.get("max_entries", 30)),
        max_per_category=int(data.get("max_per_category", 6)),
        dedupe_threshold=float(data.get("dedupe_threshold", 0.6)),
        categories=list(data.get("categories") or DEFAULT_CATEGORIES),
        llm=data.get("llm") or {},
        sources=data.get("sources") or {},
        output_dir=output_dir if output_dir.is_absolute() else root / output_dir,
        state_db=state_db if state_db.is_absolute() else root / state_db,
        skip_seen=bool(data.get("skip_seen", True)),
    )
