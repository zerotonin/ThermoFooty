"""Schema-migration shim tests (offline, in-memory SQLite).

The Phase-5b schema bump added red_count_home / red_count_away to
matches.  Pre-Phase-5b databases (which already exist on Bart's
workstation) need an ALTER TABLE to pick up the new columns.
migrate_schema() does that idempotently.
"""

from __future__ import annotations

import sqlite3

import pytest

from thermofooty.db import (
    _MATCHES_EXPECTED_COLUMNS,
    migrate_schema,
)

# A minimal pre-Phase-5b matches DDL — no red_count_* columns.
_PRE_PHASE5B_DDL = """
CREATE TABLE countries (
    country_id INTEGER PRIMARY KEY AUTOINCREMENT,
    iso_alpha2 TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

CREATE TABLE leagues (
    league_id INTEGER PRIMARY KEY AUTOINCREMENT,
    country_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    tier INTEGER NOT NULL,
    short_code TEXT NOT NULL UNIQUE,
    in_primary_panel INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE clubs (
    club_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    country_id INTEGER NOT NULL
);

CREATE TABLE stadia (
    stadium_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    country_id INTEGER NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL
);

CREATE TABLE matches (
    match_id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id INTEGER,
    season TEXT,
    match_date TEXT NOT NULL,
    home_club_id INTEGER NOT NULL,
    away_club_id INTEGER NOT NULL,
    stadium_id INTEGER NOT NULL,
    card_count_home INTEGER,
    card_count_away INTEGER,
    data_tier TEXT NOT NULL,
    source_primary TEXT NOT NULL
);
"""


@pytest.fixture
def pre_phase5b_db() -> sqlite3.Connection:
    """In-memory SQLite at the pre-Phase-5b schema (no red_count_*)."""
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(_PRE_PHASE5B_DDL)
    # Seed one row so we can verify data preservation after migration.
    c.execute("INSERT INTO countries (iso_alpha2, name) VALUES ('EN', 'England')")
    c.execute(
        "INSERT INTO leagues (country_id, name, tier, short_code) "
        "VALUES (1, 'Premier League', 1, 'EN_PREM')"
    )
    c.executemany(
        "INSERT INTO clubs (name, country_id) VALUES (?, 1)",
        [("Liverpool",), ("Man United",)],
    )
    c.execute(
        "INSERT INTO stadia (name, country_id, latitude, longitude) "
        "VALUES ('Anfield', 1, 53.43, -2.96)"
    )
    c.execute(
        "INSERT INTO matches (league_id, season, match_date, home_club_id, "
        "away_club_id, stadium_id, card_count_home, card_count_away, "
        "data_tier, source_primary) "
        "VALUES (1, '2022-23', '2022-08-15', 1, 2, 1, 3, 5, 'B', 'football_data_uk')"
    )
    c.commit()
    yield c
    c.close()


# ─────────────────────────────────────────────────────────────────
#  Migration behaviour
# ─────────────────────────────────────────────────────────────────


def test_migrate_schema_adds_missing_red_count_columns(pre_phase5b_db):
    """The pre-Phase-5b DB lacks red_count_*; migrate_schema adds them."""
    added = migrate_schema(pre_phase5b_db)
    assert sorted(added) == ["red_count_away", "red_count_home"]
    cur = pre_phase5b_db.execute("PRAGMA table_info(matches)")
    cols = {row[1] for row in cur.fetchall()}
    assert "red_count_home" in cols
    assert "red_count_away" in cols


def test_migrate_schema_preserves_existing_data(pre_phase5b_db):
    migrate_schema(pre_phase5b_db)
    cur = pre_phase5b_db.execute(
        "SELECT card_count_home, card_count_away, red_count_home, red_count_away "
        "FROM matches WHERE match_date = '2022-08-15'"
    )
    row = cur.fetchone()
    # card_count_* preserved; red_count_* new and NULL (no source data yet)
    assert int(row[0]) == 3
    assert int(row[1]) == 5
    assert row[2] is None
    assert row[3] is None


def test_migrate_schema_is_idempotent(pre_phase5b_db):
    """Running migrate_schema twice must not re-add columns or raise."""
    first = migrate_schema(pre_phase5b_db)
    assert len(first) > 0
    second = migrate_schema(pre_phase5b_db)
    assert second == []


def test_migrate_schema_returns_empty_when_matches_absent():
    """A bare DB without the matches table at all returns empty —
    the caller is expected to bootstrap from schema.sql instead.
    """
    c = sqlite3.connect(":memory:")
    try:
        added = migrate_schema(c)
        assert added == []
    finally:
        c.close()


def test_matches_expected_columns_covers_phase5b_addition():
    """Guards the dispatch table — the new red_count_* columns must
    be enumerated in _MATCHES_EXPECTED_COLUMNS or migrate_schema
    silently skips them.
    """
    assert "red_count_home" in _MATCHES_EXPECTED_COLUMNS
    assert "red_count_away" in _MATCHES_EXPECTED_COLUMNS
    assert _MATCHES_EXPECTED_COLUMNS["red_count_home"] == "INTEGER"
