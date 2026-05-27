# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — db                                                ║
# ║  « SQLite session management + alembic-lite migrations »         ║
# ╚══════════════════════════════════════════════════════════════════╝
"""SQLite session + schema-version management.

Phase 1 (scaffold): module exists, ``connect()`` + ``apply_migrations()``
are stubs that import the SQLite database from ``thermofooty.config``
and return a context-manager connection.  Schema-version tracking
arrives in Phase 2 with the first ingestion pass.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from thermofooty.config import SQLITE_PATH, assert_data_root_ready


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection with foreign keys enforced.

    Per the pre-registration (§ 3.6), every SQLite connection in
    ThermoFooty enables ``PRAGMA foreign_keys = ON`` so the
    ``lineups``-keyed cards and arrests tables fail loudly on missing
    parents rather than silently orphaning rows.
    """
    assert_data_root_ready()
    db_path = path if path is not None else SQLITE_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_migrations() -> int:
    """Apply unrun migrations from ``db/migrations/`` in numeric order.

    Phase 1 stub: returns 0 (nothing to do).  Phase 2 wires this to
    the alembic-lite directory and the ``schema_version`` table per
    the pre-registration's § 3.6 SQLite backend spec.
    """
    return 0


# ─────────────────────────────────────────────────────────────────
#  Inline schema migrations  « idempotent ALTER TABLE shim »
# ─────────────────────────────────────────────────────────────────

#: Columns we expect on ``matches`` as of the current schema, with
#: their SQL types.  Used by :func:`migrate_schema` to ALTER TABLE on
#: any pre-existing database that pre-dates a column-adding schema
#: bump.  Pre-Phase-5b databases lack ``red_count_home`` /
#: ``red_count_away``; this dict adds them via the migration shim.
_MATCHES_EXPECTED_COLUMNS: dict[str, str] = {
    "card_count_home": "INTEGER",
    "card_count_away": "INTEGER",
    "red_count_home":  "INTEGER",
    "red_count_away":  "INTEGER",
}


def _existing_matches_columns(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("PRAGMA table_info(matches)")
    return {str(row[1]) for row in cur.fetchall()}


def migrate_schema(conn: sqlite3.Connection) -> list[str]:
    """Add any missing columns on ``matches`` via idempotent ALTER TABLE.

    Returns the list of column names that were added on this call;
    empty list means the schema was already current.  Safe to call on
    every ingest invocation — the PRAGMA check + ALTER TABLE pair is
    cheap and SQLite's `ALTER TABLE ... ADD COLUMN` is a metadata-only
    operation (no row rewrite).

    Only handles the ``matches`` table for now; extend the
    ``_MATCHES_EXPECTED_COLUMNS`` mapping (and add sister functions
    for other tables) as future schema bumps land.
    """
    existing = _existing_matches_columns(conn)
    if not existing:
        # No `matches` table at all — the caller bootstraps from
        # schema.sql, no migration needed.
        return []
    added: list[str] = []
    for col, sqltype in _MATCHES_EXPECTED_COLUMNS.items():
        if col in existing:
            continue
        conn.execute(f"ALTER TABLE matches ADD COLUMN {col} {sqltype}")
        added.append(col)
    if added:
        conn.commit()
    return added
