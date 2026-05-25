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
