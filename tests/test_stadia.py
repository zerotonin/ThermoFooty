"""Stadia + club-history seed-loader tests.

Pure SQLite-in-memory; reads the committed seed CSVs but doesn't
touch the network or the on-disk database file.
"""

from __future__ import annotations

import sqlite3

import pytest

from thermofooty.config import SCHEMA_SQL_PATH
from thermofooty.sources.stadia import (
    ENGLISH_HISTORY_CSV,
    ENGLISH_STADIA_CSV,
    build_stadium_resolver,
    load_club_stadium_history,
    load_stadia,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    """In-memory SQLite with the canonical schema applied."""
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
    yield c
    c.close()


# ─────────────────────────────────────────────────────────────────
#  Seed CSVs ship with the repo
# ─────────────────────────────────────────────────────────────────


def test_seed_csvs_committed():
    assert ENGLISH_STADIA_CSV.exists(), (
        "english_stadia.csv must be committed under db/seed/ — it's curated "
        "lab knowledge, not bulk data."
    )
    assert ENGLISH_HISTORY_CSV.exists(), (
        "english_club_stadium_history.csv must be committed under db/seed/."
    )


# ─────────────────────────────────────────────────────────────────
#  load_stadia is idempotent
# ─────────────────────────────────────────────────────────────────


def test_load_stadia_returns_lookup_and_country(conn):
    lookup, country_id = load_stadia(conn)
    assert country_id > 0
    assert len(lookup) > 20, "English stadia CSV should have >20 unique grounds"
    assert "Anfield" in lookup
    assert "Old Trafford" in lookup


def test_load_stadia_covers_championship_and_l1_grounds(conn):
    """Phase 2c expansion: english_stadia.csv must carry the current
    Championship + League One squads so the multi-league ingest
    doesn't skip half the matches with 'unknown stadium' warnings.
    """
    lookup, _ = load_stadia(conn)
    # Current Championship venues (a sample, not exhaustive)
    for venue in ("Ewood Park", "Ashton Gate", "Fratton Park", "Deepdale"):
        assert venue in lookup, f"missing Championship venue {venue!r}"
    # Current League One venues
    for venue in ("Oakwell", "St Andrew's", "Adams Park", "SToK Cae Ras"):
        assert venue in lookup, f"missing League One venue {venue!r}"


def test_load_stadia_is_idempotent(conn):
    lookup_a, _ = load_stadia(conn)
    lookup_b, _ = load_stadia(conn)
    assert lookup_a == lookup_b
    cur = conn.execute("SELECT count(*) FROM stadia")
    assert int(cur.fetchone()[0]) == len(lookup_a), (
        "Second load_stadia call must not duplicate rows."
    )


# ─────────────────────────────────────────────────────────────────
#  Club + history loader
# ─────────────────────────────────────────────────────────────────


def test_load_club_history_returns_alias_map(conn):
    stadium_lookup, _ = load_stadia(conn)
    aliases, periods = load_club_stadium_history(conn, stadium_lookup)
    # football-data.co.uk uses 'Man United'; our canonical is 'Manchester United'
    assert "man united" in aliases
    assert "manchester united" in aliases
    assert aliases["man united"] == aliases["manchester united"]
    assert len(periods) > len(aliases) // 4  # multiple periods per club is fine


def test_club_aliases_cover_football_data_uk_short_forms(conn):
    """football-data.co.uk has its own short-name conventions for many
    English clubs.  The aliases column must list every variant we expect
    to encounter so the upsert path doesn't silently skip rows.
    """
    stadium_lookup, _ = load_stadia(conn)
    aliases, _ = load_club_stadium_history(conn, stadium_lookup)
    # A handful of known football-data.co.uk short forms:
    expected = [
        "nott'm forest", "spurs", "wolves", "qpr", "man city",
        "sheffield weds", "blackburn", "bristol city", "bristol rvs",
        "peterboro'", "notts county", "mk dons",
    ]
    for alias in expected:
        assert alias in aliases, f"missing football-data.co.uk alias {alias!r}"


# ─────────────────────────────────────────────────────────────────
#  Resolver  « (club_canonical, season) → stadium_id »
# ─────────────────────────────────────────────────────────────────


def test_resolver_picks_pre_2006_arsenal_to_highbury(conn):
    stadium_lookup, _ = load_stadia(conn)
    _, periods = load_club_stadium_history(conn, stadium_lookup)
    resolve = build_stadium_resolver(periods, stadium_lookup)
    sid = resolve("Arsenal", "2005-06")
    assert sid == stadium_lookup["Highbury"]


def test_resolver_picks_post_2006_arsenal_to_emirates(conn):
    stadium_lookup, _ = load_stadia(conn)
    _, periods = load_club_stadium_history(conn, stadium_lookup)
    resolve = build_stadium_resolver(periods, stadium_lookup)
    sid = resolve("Arsenal", "2010-11")
    assert sid == stadium_lookup["Emirates Stadium"]


def test_resolver_picks_2018_tottenham_to_wembley(conn):
    """Spurs spent 2017-18 and 2018-19 at Wembley while the new ground was built."""
    stadium_lookup, _ = load_stadia(conn)
    _, periods = load_club_stadium_history(conn, stadium_lookup)
    resolve = build_stadium_resolver(periods, stadium_lookup)
    sid = resolve("Tottenham", "2018-19")
    assert sid == stadium_lookup["Wembley Stadium"]


def test_resolver_returns_none_for_unknown_club(conn):
    stadium_lookup, _ = load_stadia(conn)
    _, periods = load_club_stadium_history(conn, stadium_lookup)
    resolve = build_stadium_resolver(periods, stadium_lookup)
    assert resolve("Made Up FC", "2020-21") is None
