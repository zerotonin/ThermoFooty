# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — sources/fbref_ingest                              ║
# ║  « walk schedules → match reports → SQLite lineups + cards »     ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Phase 3c: upsert orchestration.  Consumes the foundation        ║
# ║  (Phase 3a schedule parser + Phase 3b match-report parser) and   ║
# ║  drives the SQLite mutations on the players / lineups / cards    ║
# ║  tables.                                                         ║
# ║                                                                  ║
# ║  Match resolution: fbref schedule rows are joined to our         ║
# ║  matches table on (match_date, home_club_id, away_club_id) so    ║
# ║  the existing fixture rows light up with lineup + card data.     ║
# ║  fbref's club-name conventions differ from football-data.co.uk   ║
# ║  in a small set of cases (Wolverhampton Wanderers vs Wolves,     ║
# ║  Brighton & Hove Albion vs Brighton, etc.); FBREF_TO_CANONICAL   ║
# ║  bridges those without polluting the seed CSV with               ║
# ║  fbref-specific aliases.                                         ║
# ║                                                                  ║
# ║  Every insert is idempotent:                                     ║
# ║    - players keyed on fbref_id (UNIQUE in the schema)            ║
# ║    - lineups keyed on (match_id, player_id) (UNIQUE)             ║
# ║    - cards inserted unconditionally (one per timeline event)     ║
# ║                                                                  ║
# ║  data_provenance gets one row per (league, season) ingest run.   ║
# ╚══════════════════════════════════════════════════════════════════╝
"""fbref schedule + match-report ingest into SQLite."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from thermofooty.sources.fbref import (
    ParsedMatchReport,
    ScheduledMatch,
    fetch_and_parse_match_report,
    fetch_and_parse_schedule,
)

# ─────────────────────────────────────────────────────────────────
#  fbref-name -> our-canonical-name bridge
# ─────────────────────────────────────────────────────────────────

#: fbref uses long-form club names in match reports.  Our seed CSV
#: (english_club_stadium_history.csv) uses the football-data.co.uk
#: short forms.  This table bridges the difference for cases where
#: the names actually differ; identity mappings are not needed since
#: load_club_stadium_history already aliases the canonical name.
FBREF_TO_CANONICAL: dict[str, str] = {
    "Manchester Utd":           "Manchester United",
    "Manchester United":        "Manchester United",
    "Manchester City":          "Manchester City",
    "Tottenham":                "Tottenham",
    "Tottenham Hotspur":        "Tottenham",
    "Wolverhampton Wanderers":  "Wolves",
    "Wolverhampton":            "Wolves",
    "Brighton & Hove Albion":   "Brighton",
    "Brighton":                 "Brighton",
    "West Bromwich Albion":     "West Brom",
    "Nottingham Forest":        "Nott'm Forest",
    "Newcastle United":         "Newcastle",
    "Newcastle Utd":            "Newcastle",
    "Leicester City":           "Leicester",
    "Norwich City":             "Norwich",
    "Stoke City":               "Stoke",
    "Hull City":                "Hull",
    "Cardiff City":             "Cardiff",
    "Swansea City":             "Swansea",
    "Sheffield United":         "Sheffield United",
    "Sheffield Wednesday":      "Sheffield Weds",
    "Sheffield Wed":            "Sheffield Weds",
    "Queens Park Rangers":      "QPR",
    "AFC Bournemouth":          "Bournemouth",
    "Burton Albion":            "Burton",
    "Crystal Palace":           "Crystal Palace",
    "Crewe Alexandra":          "Crewe",
    "Cambridge United":         "Cambridge",
    "Lincoln City":             "Lincoln",
    "Doncaster Rovers":         "Doncaster",
    "Mansfield Town":           "Mansfield",
    "Wycombe Wanderers":        "Wycombe",
    "Stevenage Borough":        "Stevenage",
    "Notts County":             "Notts County",
    "Tranmere Rovers":          "Tranmere",
    "Carlisle United":          "Carlisle",
    "Bristol Rovers":           "Bristol Rvs",
    "Forest Green Rovers":      "Forest Green",
    "Bradford City":            "Bradford",
    "Bolton Wanderers":         "Bolton",
    "Blackburn Rovers":         "Blackburn",
    "Peterborough United":      "Peterborough",
    "Exeter City":              "Exeter",
    "Cheltenham Town":          "Cheltenham",
    "Northampton Town":         "Northampton",
    "Charlton Athletic":        "Charlton",
    "MK Dons":                  "MK Dons",
    "Milton Keynes Dons":       "MK Dons",
    "AFC Wimbledon":            "AFC Wimbledon",
    "Wrexham":                  "Wrexham",
    "Plymouth Argyle":          "Plymouth",
    "Preston North End":        "Preston",
    "Oxford United":            "Oxford",
    "Port Vale":                "Port Vale",
    "Crawley Town":             "Crawley Town",
    "Shrewsbury Town":          "Shrewsbury",
    "Coventry City":            "Coventry",
    "Bristol City":             "Bristol City",
    "Luton Town":               "Luton",
    "Burnley":                  "Burnley",
    "Liverpool":                "Liverpool",
    "Chelsea":                  "Chelsea",
    "Arsenal":                  "Arsenal",
    "Everton":                  "Everton",
    "Aston Villa":              "Aston Villa",
    "West Ham United":          "West Ham",
    "West Ham":                 "West Ham",
    "Reading":                  "Reading",
    "Watford":                  "Watford",
    "Brentford":                "Brentford",
    "Sunderland":               "Sunderland",
    "Middlesbrough":            "Middlesbrough",
    "Leeds United":             "Leeds",
    "Leeds":                    "Leeds",
    "Birmingham City":          "Birmingham",
    "Huddersfield Town":        "Huddersfield",
    "Blackpool":                "Blackpool",
    "Barnsley":                 "Barnsley",
    "Millwall":                 "Millwall",
    "Portsmouth":               "Portsmouth",
    "Walsall":                  "Walsall",
    "Wigan Athletic":           "Wigan",
    "Wigan":                    "Wigan",
    "Derby County":             "Derby",
    "Derby":                    "Derby",
    "Ipswich Town":             "Ipswich",
    "Ipswich":                  "Ipswich",
    "Fulham":                   "Fulham",
    "Rotherham United":         "Rotherham",
    "Rotherham":                "Rotherham",
    "Leyton Orient":            "Leyton Orient",
    "Morecambe":                "Morecambe",
    "Mansfield":                "Mansfield",
}


def resolve_fbref_club(
    fbref_name: str,
    alias_to_club_id: dict[str, int],
) -> int | None:
    """Map a fbref club name onto our canonical club_id, or None if absent."""
    canonical = FBREF_TO_CANONICAL.get(fbref_name, fbref_name)
    return alias_to_club_id.get(canonical.lower())


# ─────────────────────────────────────────────────────────────────
#  Match resolution  « find our match_id from fbref schedule row »
# ─────────────────────────────────────────────────────────────────


def resolve_match_id(
    conn: sqlite3.Connection,
    scheduled: ScheduledMatch,
    alias_to_club_id: dict[str, int],
) -> int | None:
    """Return our matches.match_id for a fbref schedule row, or None."""
    home_id = resolve_fbref_club(scheduled.home_team, alias_to_club_id)
    away_id = resolve_fbref_club(scheduled.away_team, alias_to_club_id)
    if home_id is None or away_id is None:
        return None
    cur = conn.execute(
        """
        SELECT match_id FROM matches
        WHERE match_date = ?
          AND home_club_id = ?
          AND away_club_id = ?
        LIMIT 1
        """,
        (scheduled.match_date.isoformat(), home_id, away_id),
    )
    row = cur.fetchone()
    return int(row[0]) if row is not None else None


# ─────────────────────────────────────────────────────────────────
#  Player + lineup + card upserts
# ─────────────────────────────────────────────────────────────────


def upsert_player(
    conn: sqlite3.Connection,
    fbref_id: str,
    name: str,
) -> int:
    """Insert player by fbref_id if absent; return our players.player_id.

    Idempotent: the schema's UNIQUE on players.fbref_id handles dedupe
    across re-runs.  Players without a fbref_id (parser miss) are
    handled by the caller — they shouldn't reach this function.
    """
    cur = conn.execute(
        "SELECT player_id FROM players WHERE fbref_id = ?", (fbref_id,),
    )
    row = cur.fetchone()
    if row is not None:
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO players (name, fbref_id) VALUES (?, ?)",
        (name, fbref_id),
    )
    return int(cur.lastrowid)


def upsert_lineup_row(
    conn: sqlite3.Connection,
    *,
    match_id: int,
    player_id: int,
    club_id: int,
    is_home: int,
    started: int,
    minutes_played: int | None,
    position: str | None,
) -> int:
    """Insert a (match, player) lineup row.  Returns lineup_id."""
    cur = conn.execute(
        "SELECT lineup_id FROM lineups WHERE match_id = ? AND player_id = ?",
        (match_id, player_id),
    )
    row = cur.fetchone()
    if row is not None:
        return int(row[0])
    cur = conn.execute(
        """
        INSERT INTO lineups (
            match_id, player_id, club_id, is_home, started,
            minutes_played, position
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (match_id, player_id, club_id, is_home, started, minutes_played, position),
    )
    return int(cur.lastrowid)


def insert_card_row(
    conn: sqlite3.Connection,
    *,
    lineup_id: int,
    match_id: int,
    minute_of_issue: int | None,
    card_color: str,
    card_reason: str | None,
    aggression_set: int,
) -> None:
    """Insert one card event.  Schema has no UNIQUE so duplicates are
    possible — callers should dedupe before invoking (the ingest
    driver below clears prior fbref cards per match before re-insert).
    """
    conn.execute(
        """
        INSERT INTO cards (
            lineup_id, match_id, minute_of_issue, card_color,
            card_reason, aggression_set, source
        ) VALUES (?, ?, ?, ?, ?, ?, 'fbref')
        """,
        (
            lineup_id, match_id, minute_of_issue, card_color,
            card_reason, aggression_set,
        ),
    )


# ─────────────────────────────────────────────────────────────────
#  End-to-end per-match upsert
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UpsertStats:
    """Per-match upsert summary for the rich progress table."""

    match_id: int
    n_lineups_written: int
    n_cards_written: int


def upsert_match_report(
    conn: sqlite3.Connection,
    *,
    match_id: int,
    home_club_id: int,
    away_club_id: int,
    parsed: ParsedMatchReport,
) -> UpsertStats:
    """Upsert one parsed match report into lineups + cards + players.

    Steps:
      1. Wipe any existing fbref cards for this match (lets re-runs
         pick up corrected aggression-set classifications without
         duplicating rows).
      2. For each team, for each player appearance, upsert the player
         then upsert the lineup row.  Build a (fbref_player_id ->
         lineup_id) map for the card step.
      3. For each card event, look up the lineup_id by fbref_player_id;
         if missing (event without a player link), skip and count.
    """
    # 1. Clear prior fbref cards for this match (idempotent re-runs)
    conn.execute(
        "DELETE FROM cards WHERE match_id = ? AND source = 'fbref'",
        (match_id,),
    )

    # 2. Lineups
    fbref_to_lineup: dict[str, int] = {}
    for team_apps, club_id in (
        (parsed.home, home_club_id),
        (parsed.away, away_club_id),
    ):
        for p in team_apps.players:
            if not p.fbref_player_id:
                continue
            player_id = upsert_player(conn, p.fbref_player_id, p.player_name)
            lineup_id = upsert_lineup_row(
                conn,
                match_id=match_id, player_id=player_id, club_id=club_id,
                is_home=team_apps.is_home, started=p.started,
                minutes_played=p.minutes_played, position=p.position,
            )
            fbref_to_lineup[p.fbref_player_id] = lineup_id

    # 3. Cards
    n_cards = 0
    for card in parsed.cards:
        if card.fbref_player_id is None:
            continue
        lineup_id = fbref_to_lineup.get(card.fbref_player_id)
        if lineup_id is None:
            continue
        insert_card_row(
            conn,
            lineup_id=lineup_id, match_id=match_id,
            minute_of_issue=card.minute_of_issue,
            card_color=card.card_color,
            card_reason=card.card_reason or None,
            aggression_set=(card.aggression_set or 0),
        )
        n_cards += 1
    return UpsertStats(
        match_id=match_id,
        n_lineups_written=len(fbref_to_lineup),
        n_cards_written=n_cards,
    )


# ─────────────────────────────────────────────────────────────────
#  Season-walker  « one (league, season) ingest pass »
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SeasonIngestStats:
    """End-of-season ingest summary."""

    league_short_code: str
    season: str
    n_scheduled: int
    n_resolved: int           # match rows found in our `matches`
    n_upserted: int           # match rows where report parsed + upserted
    n_skipped_unresolved: int
    n_failed_fetch: int


def ingest_one_season(
    conn: sqlite3.Connection,
    *,
    league_short_code: str,
    season: str,
    alias_to_club_id: dict[str, int],
    client=None,
    refetch: bool = False,
    on_match=None,
) -> SeasonIngestStats:
    """Ingest one (league, season): schedule + every match report.

    ``on_match`` (optional callable) is invoked after each match with
    ``(scheduled_match, upsert_stats | None)`` so a CLI can drive a
    Rich progress bar without coupling the library to a specific UI.
    """
    scheduled = fetch_and_parse_schedule(
        league_short_code, season, client=client, refetch=refetch,
    )
    n_resolved = 0
    n_upserted = 0
    n_skipped = 0
    n_failed = 0
    for s in scheduled:
        match_id = resolve_match_id(conn, s, alias_to_club_id)
        if match_id is None:
            n_skipped += 1
            if on_match is not None:
                on_match(s, None)
            continue
        n_resolved += 1
        home_id = resolve_fbref_club(s.home_team, alias_to_club_id)
        away_id = resolve_fbref_club(s.away_team, alias_to_club_id)
        try:
            parsed = fetch_and_parse_match_report(
                s, client=client, refetch=refetch,
            )
        except Exception as exc:  # network / parser failure on one match
            n_failed += 1
            if on_match is not None:
                on_match(s, None)
            # Log via the provenance row at the end
            del exc
            continue
        stats = upsert_match_report(
            conn,
            match_id=match_id,
            home_club_id=home_id,    # type: ignore[arg-type]
            away_club_id=away_id,    # type: ignore[arg-type]
            parsed=parsed,
        )
        n_upserted += 1
        if on_match is not None:
            on_match(s, stats)
    return SeasonIngestStats(
        league_short_code=league_short_code,
        season=season,
        n_scheduled=len(scheduled),
        n_resolved=n_resolved,
        n_upserted=n_upserted,
        n_skipped_unresolved=n_skipped,
        n_failed_fetch=n_failed,
    )


def record_provenance(
    conn: sqlite3.Connection, stats: SeasonIngestStats,
) -> None:
    """One data_provenance row per (league, season) ingest pass."""
    accessed_at = datetime.now(UTC).isoformat(timespec="seconds")
    note = (
        f"{stats.league_short_code} {stats.season}: "
        f"scheduled={stats.n_scheduled} resolved={stats.n_resolved} "
        f"upserted={stats.n_upserted} skipped={stats.n_skipped_unresolved} "
        f"failed={stats.n_failed_fetch}"
    )
    conn.execute(
        """
        INSERT INTO data_provenance (
            source, accessed_at, n_rows_pulled, sha256_payload, notes
        ) VALUES ('fbref', ?, ?, NULL, ?)
        """,
        (accessed_at, stats.n_upserted, note),
    )
