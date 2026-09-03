"""SQLite record of already-published items.

Without this, a story that stays popular for ten days shows up in two
consecutive digests. Keyed by canonical URL so cross-source duplicates collapse
to one record.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    canonical_url TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    week          TEXT NOT NULL,
    first_seen    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS seen_week_idx ON seen(week);
"""


class Store:
    """Thin wrapper over a local SQLite file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def seen_urls(self) -> set[str]:
        with closing(self._connect()) as conn:
            return {row[0] for row in conn.execute("SELECT canonical_url FROM seen")}

    def mark_seen(self, entries: Iterable[tuple[str, str]], week: str) -> int:
        """Record ``(canonical_url, title)`` pairs as published in ``week``."""
        now = datetime.now(UTC).isoformat()
        rows = [(url, title, week, now) for url, title in entries if url]
        if not rows:
            return 0
        with closing(self._connect()) as conn:
            cur = conn.executemany(
                "INSERT OR IGNORE INTO seen (canonical_url, title, week, first_seen)"
                " VALUES (?, ?, ?, ?)",
                rows,
            )
            conn.commit()
            return cur.rowcount

    def count(self) -> int:
        with closing(self._connect()) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0])
