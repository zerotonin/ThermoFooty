"""Analysis-panel materialiser tests (offline, in-memory SQLite).

Seeds a small DB with two leagues, three stadia, four clubs, and a
handful of matches with weather rows, then exercises
materialise_analysis_panel and asserts the explode-per-side shape +
the documented filters.
"""

from __future__ import annotations

import sqlite3

import pytest

from thermofooty.config import SCHEMA_SQL_PATH
from thermofooty.panel import PANEL_COLUMNS, materialise_analysis_panel


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))

    c.execute("INSERT INTO countries (iso_alpha2, name) VALUES ('EN', 'England')")
    country_id = c.execute("SELECT country_id FROM countries").fetchone()[0]

    # Two leagues: one in primary panel, one not (used to test the filter)
    c.executemany(
        "INSERT INTO leagues (country_id, name, tier, short_code, in_primary_panel) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (country_id, "Premier League", 1, "EN_PREM", 1),
            (country_id, "Conference", 5, "EN_NL", 0),  # not in panel
        ],
    )
    epl_id = c.execute(
        "SELECT league_id FROM leagues WHERE short_code = 'EN_PREM'"
    ).fetchone()[0]
    nl_id = c.execute(
        "SELECT league_id FROM leagues WHERE short_code = 'EN_NL'"
    ).fetchone()[0]

    c.executemany(
        "INSERT INTO clubs (name, country_id) VALUES (?, ?)",
        [
            ("Liverpool", country_id), ("Man United", country_id),
            ("Arsenal", country_id), ("Chelsea", country_id),
        ],
    )

    def cid(name: str) -> int:
        return c.execute("SELECT club_id FROM clubs WHERE name = ?", (name,)).fetchone()[0]

    c.executemany(
        "INSERT INTO stadia (name, country_id, city, latitude, longitude, "
        "altitude_m, has_roof, qatar2022_cooled) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        [
            ("Anfield", country_id, "Liverpool", 53.43, -2.96, 57),
            ("Old Trafford", country_id, "Manchester", 53.46, -2.29, 38),
            ("Emirates Stadium", country_id, "London", 51.55, -0.11, 33),
        ],
    )

    def sid(name: str) -> int:
        return c.execute("SELECT stadium_id FROM stadia WHERE name = ?", (name,)).fetchone()[0]

    # Insert seven matches:
    #   3 EPL with weather + cards (should appear in panel as 6 side-rows)
    #   1 EPL with weather but NULL cards (should be filtered)
    #   1 EPL with cards but no weather row (should be filtered)
    #   1 EPL with weather marked 'unverifiable' (should be filtered)
    #   1 Conference match (in_primary_panel=0; should be filtered)
    # Fields: league_id, season, date, home, away, stadium, card_h, card_a, source_tier.
    # source_tier=None means "no weather row at all"; 'unverifiable' means
    # cascade declined.  Each tuple is one match-day insert.
    matches = [
        (epl_id, "2022-23", "2022-08-15",
         "Liverpool", "Man United", "Anfield", 3, 5, "tier1_ghcn"),
        (epl_id, "2022-23", "2022-10-08",
         "Arsenal", "Chelsea", "Emirates Stadium", 2, 4, "tier1_ghcn"),
        (epl_id, "2022-23", "2023-03-05",
         "Man United", "Liverpool", "Old Trafford", 1, 0, "tier2_hadcet_max"),
        (epl_id, "2022-23", "2023-04-15",
         "Liverpool", "Arsenal", "Anfield", None, None, "tier1_ghcn"),
        (epl_id, "2022-23", "2023-05-01",
         "Chelsea", "Liverpool", "Emirates Stadium", 2, 3, None),
        (epl_id, "2022-23", "2023-05-10",
         "Arsenal", "Liverpool", "Emirates Stadium", 2, 2, "unverifiable"),
        (nl_id, "2022-23", "2022-09-01",
         "Liverpool", "Arsenal", "Anfield", 1, 1, "tier1_ghcn"),
    ]
    for league_id, season, mdate, home, away, stadium, ch, ca, tier in matches:
        c.execute(
            """
            INSERT INTO matches (
                league_id, season, match_date, home_club_id, away_club_id,
                stadium_id, card_count_home, card_count_away,
                data_tier, source_primary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'B', 'football_data_uk')
            """,
            (league_id, season, mdate, cid(home), cid(away), sid(stadium), ch, ca),
        )
        if tier is not None:
            c.execute(
                "INSERT INTO weather (stadium_id, observation_date, "
                "tmax_obs_c, tmax_anomaly_c, baseline_mean_c, source_tier) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sid(stadium), mdate, 22.0, 1.5, 20.5, tier),
            )
    c.commit()
    yield c
    c.close()


# ─────────────────────────────────────────────────────────────────
#  Shape + columns
# ─────────────────────────────────────────────────────────────────


def test_panel_has_canonical_column_order(conn):
    panel = materialise_analysis_panel(conn)
    assert list(panel.columns) == PANEL_COLUMNS


def test_panel_explodes_to_two_rows_per_match(conn):
    panel = materialise_analysis_panel(conn)
    # 3 EPL matches passed all filters -> 6 side-rows
    assert len(panel) == 6
    assert (panel["side"] == "home").sum() == 3
    assert (panel["side"] == "away").sum() == 3


def test_panel_is_home_flag_matches_side(conn):
    panel = materialise_analysis_panel(conn)
    assert ((panel["side"] == "home") == (panel["is_home"] == 1)).all()


# ─────────────────────────────────────────────────────────────────
#  Filter behaviour
# ─────────────────────────────────────────────────────────────────


def test_panel_excludes_non_primary_league(conn):
    panel = materialise_analysis_panel(conn)
    assert (panel["league_short_code"] == "EN_NL").sum() == 0


def test_panel_excludes_unverifiable_weather(conn):
    panel = materialise_analysis_panel(conn)
    assert (panel["source_tier"] == "unverifiable").sum() == 0


def test_panel_excludes_matches_with_null_cards(conn):
    panel = materialise_analysis_panel(conn)
    # The 2023-04-15 match had NULL cards — it must be absent
    assert "2023-04-15" not in panel["match_date"].astype(str).values


def test_panel_excludes_matches_with_no_weather_row(conn):
    panel = materialise_analysis_panel(conn)
    # The 2023-05-01 match had no weather row at all
    assert "2023-05-01" not in panel["match_date"].astype(str).values


# ─────────────────────────────────────────────────────────────────
#  Outcome columns
# ─────────────────────────────────────────────────────────────────


def test_side_received_card_is_binary_from_n_cards_total(conn):
    panel = materialise_analysis_panel(conn)
    # Man United (home) 2023-03-05 had card_count_home = 1 -> received_card = 1
    # Liverpool (away) same match had card_count_away = 0 -> received_card = 0
    mu_row = panel[
        (panel["match_date"].astype(str) == "2023-03-05") & (panel["side"] == "home")
    ].iloc[0]
    liv_row = panel[
        (panel["match_date"].astype(str) == "2023-03-05") & (panel["side"] == "away")
    ].iloc[0]
    assert int(mu_row["n_cards_total"]) == 1
    assert int(mu_row["side_received_card"]) == 1
    assert int(liv_row["n_cards_total"]) == 0
    assert int(liv_row["side_received_card"]) == 0


def test_side_received_red_is_na_until_phase3(conn):
    """Phase 5a runs on the card proxy; the red column stays NA until
    fbref ingestion populates per-card reason codes.
    """
    panel = materialise_analysis_panel(conn)
    assert panel["side_received_red"].isna().all()
