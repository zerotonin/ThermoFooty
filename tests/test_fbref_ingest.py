"""fbref ingest-orchestration tests (offline, in-memory SQLite + fixture).

Covers Phase 3c: the schedule -> match-report -> SQLite upsert path.
Uses an in-memory DB seeded with the seed stadia / clubs + a single
real match row so we can verify the resolution + upsert plumbing
end-to-end with the committed HTML fixtures.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from thermofooty.config import SCHEMA_SQL_PATH
from thermofooty.sources.fbref import (
    RateLimitedClient,
    ScheduledMatch,
    parse_match_report,
    parse_schedule_html,
)
from thermofooty.sources.fbref_ingest import (
    FBREF_TO_CANONICAL,
    resolve_fbref_club,
    resolve_match_id,
    upsert_match_report,
)
from thermofooty.sources.stadia import (
    load_club_stadium_history,
    load_stadia,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "fbref"
SCHEDULE_HTML = (FIXTURE_DIR / "schedule_epl_2023_24_excerpt.html").read_bytes()
REPORT_HTML = (FIXTURE_DIR / "match_report_aa1de559_excerpt.html").read_bytes()


# ─────────────────────────────────────────────────────────────────
#  In-memory DB fixture with the real seed CSVs loaded
# ─────────────────────────────────────────────────────────────────


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
    yield c
    c.close()


@pytest.fixture
def seeded(conn):
    """In-memory DB with stadia + clubs + EPL league + the
    aa1de559 match (Man City vs Burnley, 2023-08-15) pre-inserted.
    """
    stadium_lookup, country_id = load_stadia(conn)
    aliases, _periods = load_club_stadium_history(conn, stadium_lookup)
    # Add the EPL league row
    cur = conn.execute(
        "INSERT INTO leagues (country_id, name, tier, short_code, in_primary_panel) "
        "VALUES (?, 'Premier League', 1, 'EN_PREM', 1)",
        (country_id,),
    )
    league_id = int(cur.lastrowid)
    # Pre-insert the match the fixture corresponds to (date in fbref
    # fixture is 2023-08-11; team mapping: Manchester City home, Burnley away)
    home_id = aliases["man city"]
    away_id = aliases["burnley"]
    cur = conn.execute(
        "INSERT INTO matches (league_id, season, match_date, home_club_id, "
        "away_club_id, stadium_id, data_tier, source_primary) "
        "VALUES (?, '2023-24', '2023-08-11', ?, ?, ?, 'B', 'football_data_uk')",
        (league_id, home_id, away_id, stadium_lookup["Etihad Stadium"]),
    )
    match_id = int(cur.lastrowid)
    conn.commit()
    return {
        "conn": conn,
        "aliases": aliases,
        "stadium_lookup": stadium_lookup,
        "league_id": league_id,
        "match_id": match_id,
        "home_club_id": home_id,
        "away_club_id": away_id,
    }


# ─────────────────────────────────────────────────────────────────
#  fbref-name -> canonical bridge
# ─────────────────────────────────────────────────────────────────


def test_fbref_to_canonical_handles_long_form_names():
    """fbref's long-form names map onto our football-data.co.uk-shaped
    aliases so the seed CSV doesn't need a parallel fbref column.
    """
    assert FBREF_TO_CANONICAL["Wolverhampton Wanderers"] == "Wolves"
    assert FBREF_TO_CANONICAL["Brighton & Hove Albion"] == "Brighton"
    assert FBREF_TO_CANONICAL["West Bromwich Albion"] == "West Brom"
    assert FBREF_TO_CANONICAL["Nottingham Forest"] == "Nott'm Forest"


def test_resolve_fbref_club_returns_our_club_id(seeded):
    aliases = seeded["aliases"]
    cid_manchester = resolve_fbref_club("Manchester City", aliases)
    assert cid_manchester == aliases["man city"]
    cid_wolves = resolve_fbref_club("Wolverhampton Wanderers", aliases)
    assert cid_wolves == aliases["wolves"]


def test_resolve_fbref_club_returns_none_for_unknown(seeded):
    assert resolve_fbref_club("Atlantis FC", seeded["aliases"]) is None


# ─────────────────────────────────────────────────────────────────
#  resolve_match_id — fbref schedule row -> our match
# ─────────────────────────────────────────────────────────────────


def test_resolve_match_id_finds_pre_inserted_match(seeded):
    scheduled = parse_schedule_html(SCHEDULE_HTML)
    aug11 = next(s for s in scheduled if s.match_date == date(2023, 8, 11))
    match_id = resolve_match_id(seeded["conn"], aug11, seeded["aliases"])
    assert match_id == seeded["match_id"]


def test_resolve_match_id_returns_none_when_match_absent(seeded):
    """A scheduled match with no row in our matches table must return
    None (the caller will skip / log it).
    """
    fake = ScheduledMatch(
        fbref_match_id="00000000",
        match_report_url="https://fbref.com/en/matches/00000000/whatever",
        match_date=date(2023, 8, 12),  # different date from any in `matches`
        home_team="Brentford",
        away_team="Tottenham",
        venue="Gtech Community Stadium",
        referee=None,
    )
    assert resolve_match_id(seeded["conn"], fake, seeded["aliases"]) is None


# ─────────────────────────────────────────────────────────────────
#  upsert_match_report — players + lineups + cards
# ─────────────────────────────────────────────────────────────────


def test_upsert_match_report_writes_lineups_and_cards(seeded):
    parsed = parse_match_report("aa1de559", REPORT_HTML)
    stats = upsert_match_report(
        seeded["conn"],
        match_id=seeded["match_id"],
        home_club_id=seeded["home_club_id"],
        away_club_id=seeded["away_club_id"],
        parsed=parsed,
    )
    # The fixture has 4 home + 3 away players = 7 lineup rows
    assert stats.n_lineups_written == 7
    # 3 card events (1 yellow home, 1 red away, 1 yellow away)
    assert stats.n_cards_written == 3

    conn = seeded["conn"]
    n_players = int(conn.execute("SELECT count(*) FROM players").fetchone()[0])
    n_lineups = int(conn.execute("SELECT count(*) FROM lineups").fetchone()[0])
    n_cards = int(conn.execute("SELECT count(*) FROM cards").fetchone()[0])
    assert n_players == 7
    assert n_lineups == 7
    assert n_cards == 3


def test_upsert_match_report_preserves_aggression_set(seeded):
    """The Foster red card is annotated 'Violent conduct' in the fixture;
    aggression_set must land as 1 in the cards table.
    """
    parsed = parse_match_report("aa1de559", REPORT_HTML)
    upsert_match_report(
        seeded["conn"],
        match_id=seeded["match_id"],
        home_club_id=seeded["home_club_id"],
        away_club_id=seeded["away_club_id"],
        parsed=parsed,
    )
    conn = seeded["conn"]
    cur = conn.execute(
        "SELECT card_color, card_reason, aggression_set FROM cards "
        "WHERE card_color IN ('red', 'second_yellow_red')"
    )
    row = cur.fetchone()
    assert row is not None
    assert row[1] == "Violent conduct"
    assert int(row[2]) == 1


def test_upsert_match_report_is_idempotent(seeded):
    """Re-running upsert on the same match must produce the same row
    counts — lineups dedupe via UNIQUE, cards are wiped + re-inserted.
    """
    parsed = parse_match_report("aa1de559", REPORT_HTML)
    upsert_match_report(
        seeded["conn"],
        match_id=seeded["match_id"],
        home_club_id=seeded["home_club_id"],
        away_club_id=seeded["away_club_id"],
        parsed=parsed,
    )
    n_lineups_a = int(seeded["conn"].execute("SELECT count(*) FROM lineups").fetchone()[0])
    n_cards_a = int(seeded["conn"].execute("SELECT count(*) FROM cards").fetchone()[0])

    upsert_match_report(
        seeded["conn"],
        match_id=seeded["match_id"],
        home_club_id=seeded["home_club_id"],
        away_club_id=seeded["away_club_id"],
        parsed=parsed,
    )
    n_lineups_b = int(seeded["conn"].execute("SELECT count(*) FROM lineups").fetchone()[0])
    n_cards_b = int(seeded["conn"].execute("SELECT count(*) FROM cards").fetchone()[0])
    assert (n_lineups_a, n_cards_a) == (n_lineups_b, n_cards_b)


def test_upsert_match_report_records_minute_of_issue(seeded):
    """Per-card minute_of_issue is the field the data_tier='A' analyses
    consume — must not get dropped on the way from event parser to row.
    """
    parsed = parse_match_report("aa1de559", REPORT_HTML)
    upsert_match_report(
        seeded["conn"],
        match_id=seeded["match_id"],
        home_club_id=seeded["home_club_id"],
        away_club_id=seeded["away_club_id"],
        parsed=parsed,
    )
    cur = seeded["conn"].execute(
        "SELECT minute_of_issue FROM cards WHERE card_color = 'red'"
    )
    assert int(cur.fetchone()[0]) == 72


# ─────────────────────────────────────────────────────────────────
#  RateLimitedClient smoke check  « used by the CLI »
# ─────────────────────────────────────────────────────────────────


def test_rate_limited_client_default_is_three_seconds():
    """Phase 3 default rate limit must stay at the worldfootballR
    community convention (1 req per 3 seconds) — accidentally
    bumping this risks getting our scraper IP-banned.
    """
    client = RateLimitedClient()
    assert client.min_interval_s == 3.0
