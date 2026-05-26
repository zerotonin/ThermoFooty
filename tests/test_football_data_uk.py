"""football-data.co.uk parser + upsert tests (offline, fixture CSV).

These tests never touch the network — they parse hand-crafted CSV
fixtures shaped like the real upstream rows and exercise the upsert
through an in-memory SQLite.  A separate @pytest.mark.network test
hits one real season for the weekly cron.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from thermofooty.config import SCHEMA_SQL_PATH
from thermofooty.sources.football_data_uk import (
    FIRST_SEASON,
    LEAGUE_CODES,
    LEAGUE_METADATA,
    ParsedMatch,
    _coerce_cards,
    _coerce_int,
    _parse_date,
    all_seasons_for,
    parse_season_csv,
    season_to_url_token,
    season_url,
    upsert_matches,
)
from thermofooty.sources.stadia import (
    build_stadium_resolver,
    load_club_stadium_history,
    load_stadia,
)

# ─────────────────────────────────────────────────────────────────
#  URL builder
# ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "season, expected",
    [
        ("1993-94", "9394"),
        ("1999-2000", "9900"),
        ("2000-01", "0001"),
        ("2023-24", "2324"),
    ],
)
def test_season_to_url_token(season, expected):
    assert season_to_url_token(season) == expected


def test_season_url_premier_league_2023_24():
    url = season_url("EN_PREM", "2023-24")
    assert url.endswith("/2324/E0.csv")


def test_unknown_league_short_code_raises():
    with pytest.raises(KeyError):
        season_url("XX_NONE", "2023-24")


def test_all_known_league_codes_present():
    """The dev plan locks tier-1 EPL + tier-2 Championship + tier-3 L1."""
    for code in ("EN_PREM", "EN_CHAMP", "EN_L1"):
        assert code in LEAGUE_CODES


def test_first_season_covers_primary_panel_english_leagues():
    """Phase 2c extends FIRST_SEASON to the three primary-panel English
    leagues so all_seasons_for() works for each without a KeyError.
    """
    for code in ("EN_PREM", "EN_CHAMP", "EN_L1"):
        assert code in FIRST_SEASON
        # 1993-94 is the football-data.co.uk coverage floor for England
        assert FIRST_SEASON[code] == "1993-94"


def test_league_metadata_has_name_and_tier():
    """The CLI script uses LEAGUE_METADATA to bootstrap leagues rows."""
    name, tier = LEAGUE_METADATA["EN_CHAMP"]
    assert name == "Championship"
    assert tier == 2
    name, tier = LEAGUE_METADATA["EN_L1"]
    assert name == "League One"
    assert tier == 3
    name, tier = LEAGUE_METADATA["EN_L2"]
    assert name == "League Two"
    assert tier == 4


# ─────────────────────────────────────────────────────────────────
#  Date + coercion helpers
# ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("14/08/2023", "2023-08-14"),
        ("14/08/23", "2023-08-14"),
        (" 01/01/2000 ", "2000-01-01"),
    ],
)
def test_parse_date_accepts_both_year_widths(raw, expected):
    assert _parse_date(raw) == expected


def test_parse_date_rejects_garbage():
    with pytest.raises(ValueError):
        _parse_date("not-a-date")


def test_coerce_int_handles_blank_and_float():
    assert _coerce_int("") is None
    assert _coerce_int(None) is None
    assert _coerce_int("3") == 3
    assert _coerce_int("3.0") == 3
    assert _coerce_int("garbage") is None


def test_coerce_cards_sums_yellows_and_reds():
    assert _coerce_cards("3", "1") == 4
    assert _coerce_cards("3", "") == 3
    assert _coerce_cards("", "1") == 1
    assert _coerce_cards("", "") is None
    assert _coerce_cards(None, None) is None


# ─────────────────────────────────────────────────────────────────
#  Parser  « fixture CSV shaped like the real upstream »
# ─────────────────────────────────────────────────────────────────


@pytest.fixture
def fixture_csv(tmp_path: Path) -> Path:
    """Three-row CSV that mimics football-data.co.uk's EPL season shape."""
    path = tmp_path / "e0_fixture.csv"
    path.write_text(
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR,Referee\n"
        "E0,14/08/2023,Man United,Arsenal,2,1,H,1,0,H,12,9,5,3,11,14,4,2,3,4,0,1,A. Marriner\n"
        "E0,15/08/2023,Liverpool,Chelsea,1,1,D,1,1,D,15,10,6,4,9,12,7,3,2,2,0,0,M. Oliver\n"
        "E0,16/08/2023,Tottenham,Man City,0,2,A,0,1,A,8,17,2,8,13,10,3,9,5,1,1,0,M. Atkinson\n"
        "\n"   # blank trailing line that the parser must skip
        ",,,,,,,,,,,,,,,,,,,,,,\n"
        ,
        encoding="utf-8",
    )
    return path


def test_parse_season_csv_returns_three_matches(fixture_csv):
    matches = parse_season_csv(fixture_csv)
    assert len(matches) == 3
    assert all(isinstance(m, ParsedMatch) for m in matches)


def test_parse_season_csv_converts_dates_to_iso(fixture_csv):
    matches = parse_season_csv(fixture_csv)
    assert matches[0].date_iso == "2023-08-14"
    assert matches[1].date_iso == "2023-08-15"


def test_parse_season_csv_carries_card_aggregates(fixture_csv):
    matches = parse_season_csv(fixture_csv)
    # Man United (HY=3, HR=0) vs Arsenal (AY=4, AR=1) → 3 home, 5 away
    assert matches[0].card_count_home == 3
    assert matches[0].card_count_away == 5


# ─────────────────────────────────────────────────────────────────
#  End-to-end upsert into in-memory SQLite
# ─────────────────────────────────────────────────────────────────


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
    yield c
    c.close()


def _ensure_epl_league(conn, country_id):
    cur = conn.execute(
        """
        INSERT INTO leagues (country_id, name, tier, short_code, in_primary_panel)
        VALUES (?, 'Premier League', 1, 'EN_PREM', 1)
        """,
        (country_id,),
    )
    return int(cur.lastrowid)


def test_upsert_matches_inserts_three_rows(fixture_csv, conn):
    stadium_lookup, country_id = load_stadia(conn)
    aliases, periods = load_club_stadium_history(conn, stadium_lookup)
    resolver = build_stadium_resolver(periods, stadium_lookup)
    league_id = _ensure_epl_league(conn, country_id)

    parsed = parse_season_csv(fixture_csv)
    before = conn.total_changes
    skipped = upsert_matches(
        conn, league_id=league_id, season="2023-24",
        country_id=country_id, matches=parsed,
        alias_to_club_id=aliases, stadium_resolver=resolver,
        source_url="https://example.invalid/2324/E0.csv",
    )
    inserted = conn.total_changes - before
    assert skipped == 0
    # 3 INSERTs into matches + 3 INSERTs into referees (one per row, all distinct)
    # but conn.total_changes counts every row write, so the relationship is
    # 3 matches + 3 referees = 6 changes.
    assert inserted >= 3


def test_upsert_matches_is_idempotent(fixture_csv, conn):
    stadium_lookup, country_id = load_stadia(conn)
    aliases, periods = load_club_stadium_history(conn, stadium_lookup)
    resolver = build_stadium_resolver(periods, stadium_lookup)
    league_id = _ensure_epl_league(conn, country_id)

    parsed = parse_season_csv(fixture_csv)
    upsert_matches(
        conn, league_id=league_id, season="2023-24",
        country_id=country_id, matches=parsed,
        alias_to_club_id=aliases, stadium_resolver=resolver,
        source_url="https://example.invalid/2324/E0.csv",
    )
    cur = conn.execute("SELECT count(*) FROM matches")
    n_first = int(cur.fetchone()[0])

    # Second call must not duplicate
    upsert_matches(
        conn, league_id=league_id, season="2023-24",
        country_id=country_id, matches=parsed,
        alias_to_club_id=aliases, stadium_resolver=resolver,
        source_url="https://example.invalid/2324/E0.csv",
    )
    cur = conn.execute("SELECT count(*) FROM matches")
    n_second = int(cur.fetchone()[0])
    assert n_first == n_second == 3


def test_upsert_skips_unknown_clubs(tmp_path, conn):
    path = tmp_path / "garbage.csv"
    path.write_text(
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,HY,AY,HR,AR,Referee\n"
        "E0,14/08/2023,Atlantis FC,El Dorado,1,1,D,0,0,D,1,1,0,0,Anon\n",
        encoding="utf-8",
    )
    stadium_lookup, country_id = load_stadia(conn)
    aliases, periods = load_club_stadium_history(conn, stadium_lookup)
    resolver = build_stadium_resolver(periods, stadium_lookup)
    league_id = _ensure_epl_league(conn, country_id)

    parsed = parse_season_csv(path)
    skipped = upsert_matches(
        conn, league_id=league_id, season="2023-24",
        country_id=country_id, matches=parsed,
        alias_to_club_id=aliases, stadium_resolver=resolver,
        source_url="x",
    )
    assert skipped == 1
    cur = conn.execute("SELECT count(*) FROM matches")
    assert int(cur.fetchone()[0]) == 0


# ─────────────────────────────────────────────────────────────────
#  Season range helper
# ─────────────────────────────────────────────────────────────────


def test_all_seasons_for_epl_starts_at_1993_94():
    seasons = all_seasons_for("EN_PREM", "1995-96")
    assert seasons == ["1993-94", "1994-95", "1995-96"]


def test_all_seasons_for_includes_1999_2000_boundary():
    seasons = all_seasons_for("EN_PREM", "2000-01")
    assert "1999-2000" in seasons
    assert "2000-01" in seasons
