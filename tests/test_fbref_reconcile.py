"""fbref vs football-data.co.uk reconciliation tests (offline, in-memory DB).

Phase 3d coverage: per-match deltas, tolerance gate, per-(league,
season) summary aggregation.
"""

from __future__ import annotations

import sqlite3

import pytest

from thermofooty.config import SCHEMA_SQL_PATH
from thermofooty.sources.fbref_reconcile import (
    by_league_season,
    per_match_mismatches,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))

    c.execute("INSERT INTO countries (iso_alpha2, name) VALUES ('EN', 'England')")
    country_id = c.execute("SELECT country_id FROM countries").fetchone()[0]
    cur = c.execute(
        "INSERT INTO leagues (country_id, name, tier, short_code, in_primary_panel) "
        "VALUES (?, 'Premier League', 1, 'EN_PREM', 1)",
        (country_id,),
    )
    league_id = int(cur.lastrowid)

    c.executemany(
        "INSERT INTO clubs (name, country_id) VALUES (?, ?)",
        [("Liverpool", country_id), ("Man United", country_id),
         ("Arsenal", country_id), ("Chelsea", country_id)],
    )
    cids = {
        name: int(c.execute("SELECT club_id FROM clubs WHERE name=?", (name,)).fetchone()[0])
        for name in ("Liverpool", "Man United", "Arsenal", "Chelsea")
    }

    c.execute(
        "INSERT INTO stadia (name, country_id, latitude, longitude, "
        "altitude_m, has_roof, qatar2022_cooled) "
        "VALUES ('Anfield', ?, 53.43, -2.96, 57, 0, 0)",
        (country_id,),
    )
    stad_id = int(c.execute("SELECT stadium_id FROM stadia").fetchone()[0])

    c.executemany(
        "INSERT INTO players (name, fbref_id) VALUES (?, ?)",
        [
            ("Mo Salah", "p001"),
            ("Virgil van Dijk", "p002"),
            ("Bruno Fernandes", "p003"),
            ("Bukayo Saka", "p004"),
            ("Reece James", "p005"),
        ],
    )
    pids = {
        name: int(c.execute("SELECT player_id FROM players WHERE name=?", (name,)).fetchone()[0])
        for name in ("Mo Salah", "Virgil van Dijk", "Bruno Fernandes",
                     "Bukayo Saka", "Reece James")
    }

    # Three matches:
    #   M1: card_count_home=2 / away=3.  fbref-sum matches exactly.
    #   M2: card_count_home=1 / away=1.  fbref-sum is home=2 away=1 -> Δhome=+1 (within tolerance).
    #   M3: card_count_home=0 / away=5.  fbref-sum is home=3 away=0 -> off by 3 each side (over).
    matches_seed = [
        ("2023-08-15", "Liverpool", "Man United", 2, 3),
        ("2023-09-10", "Arsenal", "Chelsea", 1, 1),
        ("2023-10-05", "Liverpool", "Arsenal", 0, 5),
    ]
    matches = []
    for mdate, home, away, ch, ca in matches_seed:
        cur = c.execute(
            "INSERT INTO matches (league_id, season, match_date, home_club_id, "
            "away_club_id, stadium_id, card_count_home, card_count_away, "
            "data_tier, source_primary) "
            "VALUES (?, '2023-24', ?, ?, ?, ?, ?, ?, 'B', 'football_data_uk')",
            (league_id, mdate, cids[home], cids[away], stad_id, ch, ca),
        )
        matches.append((int(cur.lastrowid), home, away))

    # Per-match fbref card events (we synth them via lineups + cards)
    fbref_card_layout = {
        matches[0][0]: [  # M1: home=2 away=3 (match)
            ("Mo Salah", "Liverpool", 1, "yellow"),
            ("Virgil van Dijk", "Liverpool", 1, "yellow"),
            ("Bruno Fernandes", "Man United", 0, "yellow"),
            ("Bruno Fernandes", "Man United", 0, "yellow"),
            ("Bruno Fernandes", "Man United", 0, "red"),
        ],
        matches[1][0]: [  # M2: home=2 (Δ +1) away=1 (match)
            ("Bukayo Saka", "Arsenal", 1, "yellow"),
            ("Bukayo Saka", "Arsenal", 1, "yellow"),
            ("Reece James", "Chelsea", 0, "yellow"),
        ],
        matches[2][0]: [  # M3: home=3 (Δ +3) away=0 (Δ -5) — both over tolerance
            ("Mo Salah", "Liverpool", 1, "yellow"),
            ("Virgil van Dijk", "Liverpool", 1, "yellow"),
            ("Virgil van Dijk", "Liverpool", 1, "red"),
        ],
    }
    for match_id, events in fbref_card_layout.items():
        for player, club, is_home, color in events:
            # Reuse a single lineup per (match, player); SQLite UNIQUE handles it
            cur = c.execute(
                "SELECT lineup_id FROM lineups WHERE match_id = ? AND player_id = ?",
                (match_id, pids[player]),
            )
            row = cur.fetchone()
            if row is None:
                cur = c.execute(
                    "INSERT INTO lineups (match_id, player_id, club_id, "
                    "is_home, started) VALUES (?, ?, ?, ?, 1)",
                    (match_id, pids[player], cids[club], is_home),
                )
                lineup_id = int(cur.lastrowid)
            else:
                lineup_id = int(row[0])
            c.execute(
                "INSERT INTO cards (lineup_id, match_id, card_color, source) "
                "VALUES (?, ?, ?, 'fbref')",
                (lineup_id, match_id, color),
            )
    c.commit()
    yield c
    c.close()


# ─────────────────────────────────────────────────────────────────
#  per_match_mismatches
# ─────────────────────────────────────────────────────────────────


def test_per_match_mismatches_excludes_perfect_and_within_tolerance(conn):
    """M1 is exact (no row), M2 is within Δ=1 (no row), M3 is over (1 row)."""
    mismatches = per_match_mismatches(conn, tolerance=1)
    assert len(mismatches) == 1
    only = mismatches[0]
    assert only.delta_home == 3
    assert only.delta_away == -5


def test_per_match_mismatches_respects_tolerance(conn):
    """Tolerance=0 flags M2 (Δ=+1 home) AND M3 (Δ=+3/-5).  Should be 2 rows."""
    mismatches = per_match_mismatches(conn, tolerance=0)
    assert len(mismatches) == 2


def test_per_match_mismatches_carries_match_metadata(conn):
    mismatches = per_match_mismatches(conn, tolerance=1)
    only = mismatches[0]
    assert only.match_date == "2023-10-05"
    assert only.home_name == "Liverpool"
    assert only.away_name == "Arsenal"
    assert only.league_short_code == "EN_PREM"
    assert only.season == "2023-24"


# ─────────────────────────────────────────────────────────────────
#  by_league_season
# ─────────────────────────────────────────────────────────────────


def test_by_league_season_aggregates_three_matches(conn):
    rows = by_league_season(conn, tolerance=1)
    assert len(rows) == 1
    r = rows[0]
    assert r.league_short_code == "EN_PREM"
    assert r.season == "2023-24"
    assert r.n_matches_with_both_sources == 3
    assert r.n_perfect_match == 1
    assert r.n_within_tolerance == 2
    assert r.n_over_tolerance == 1


def test_by_league_season_mean_abs_delta_arithmetic(conn):
    """|Δhome| values are: 0, 1, 3 → mean 4/3.
    |Δaway| values are: 0, 0, 5 → mean 5/3.
    """
    rows = by_league_season(conn, tolerance=1)
    r = rows[0]
    assert r.mean_abs_delta_home == pytest.approx(4 / 3)
    assert r.mean_abs_delta_away == pytest.approx(5 / 3)


def test_by_league_season_ignores_matches_without_fbref_cards(conn):
    """A match with NULL fbref-events (never ingested via fbref) must
    not show up in the reconciliation summary.
    """
    conn.execute(
        "INSERT INTO matches (league_id, season, match_date, home_club_id, "
        "away_club_id, stadium_id, card_count_home, card_count_away, "
        "data_tier, source_primary) "
        "SELECT league_id, '2023-24', '2024-01-01', home_club_id, away_club_id, "
        "stadium_id, 2, 2, 'B', 'football_data_uk' FROM matches LIMIT 1"
    )
    conn.commit()
    rows = by_league_season(conn, tolerance=1)
    r = rows[0]
    # Still 3 matches (the new one has no fbref cards, so it's excluded)
    assert r.n_matches_with_both_sources == 3


def test_per_match_mismatches_skips_matches_without_football_data_card_counts(conn):
    """A match where card_count_home/away are NULL must be skipped
    even if fbref cards exist — there's nothing to reconcile against.
    """
    conn.execute(
        "UPDATE matches SET card_count_home = NULL, card_count_away = NULL "
        "WHERE match_date = '2023-10-05'"
    )
    conn.commit()
    mismatches = per_match_mismatches(conn, tolerance=1)
    assert len(mismatches) == 0
