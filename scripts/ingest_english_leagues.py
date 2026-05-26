# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — scripts/ingest_english_leagues                    ║
# ║  « Phase 2c: EPL + Championship + League One ingestion »         ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Bootstraps the SQLite database from db/schema.sql if it doesn't ║
# ║  exist yet, loads the English stadia + club-history seed CSVs,   ║
# ║  then downloads + parses every requested (league, season) pair.  ║
# ║                                                                  ║
# ║  --league accepts:                                               ║
# ║    EN_PREM   English Premier League (tier 1)                     ║
# ║    EN_CHAMP  Championship (tier 2)                               ║
# ║    EN_L1     League One (tier 3, in primary panel)               ║
# ║    EN_L2     League Two (tier 4, contingency panel)              ║
# ║    all       all four above                                      ║
# ║  Comma-separated combinations (e.g. 'EN_PREM,EN_CHAMP') also     ║
# ║  work.  Default is EN_PREM to keep the most-common invocation    ║
# ║  short.                                                          ║
# ║                                                                  ║
# ║  Idempotent: re-running the script is safe and only inserts new  ║
# ║  rows (the UNIQUE constraint on matches handles dedupe at the    ║
# ║  SQLite level).                                                  ║
# ║                                                                  ║
# ║  Network-touching.  CSV cache lives under                        ║
# ║  $THERMOFOOTY_DATA_ROOT/raw/football_data_uk/ so subsequent      ║
# ║  re-parses skip the network entirely.                            ║
# ╚══════════════════════════════════════════════════════════════════╝
"""CLI entry point for English football-data.co.uk match ingestion."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date

from rich.console import Console
from rich.table import Table

from thermofooty.config import SCHEMA_SQL_PATH, assert_data_root_ready
from thermofooty.db import connect
from thermofooty.sources.football_data_uk import (
    LEAGUE_METADATA,
    all_seasons_for,
    ingest_season,
)
from thermofooty.sources.stadia import (
    build_stadium_resolver,
    load_club_stadium_history,
    load_stadia,
)


# ─────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────


def _current_season_string() -> str:
    """Return e.g. '2025-26' for the season that started in the most recent August."""
    today = date.today()
    start_year = today.year if today.month >= 8 else today.year - 1
    end_two = (start_year + 1) % 100
    return f"{start_year}-{end_two:02d}"


def _bootstrap_schema_if_empty(conn: sqlite3.Connection) -> bool:
    """Apply db/schema.sql if the database is empty.  Returns True if applied."""
    cur = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='matches'"
    )
    if int(cur.fetchone()[0]) == 1:
        return False
    sql = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    return True


def _ensure_league(
    conn: sqlite3.Connection, short_code: str, country_id: int,
) -> int:
    """Insert leagues row for ``short_code`` if absent; return league_id."""
    cur = conn.execute(
        "SELECT league_id FROM leagues WHERE short_code = ?", (short_code,),
    )
    row = cur.fetchone()
    if row is not None:
        return int(row[0])
    name, tier = LEAGUE_METADATA[short_code]
    # League Two (tier 4) is contingency panel only per the OSF pre-reg.
    in_primary = 0 if tier >= 4 else 1
    cur = conn.execute(
        """
        INSERT INTO leagues (country_id, name, tier, short_code, in_primary_panel)
        VALUES (?, ?, ?, ?, ?)
        """,
        (country_id, name, tier, short_code, in_primary),
    )
    return int(cur.lastrowid)


# ─────────────────────────────────────────────────────────────────
#  Argument parsing
# ─────────────────────────────────────────────────────────────────


def _parse_leagues(value: str) -> list[str]:
    """Expand the ``--league`` argument into a list of short_codes."""
    if value == "all":
        return list(LEAGUE_METADATA)
    parts = [v.strip() for v in value.split(",") if v.strip()]
    unknown = [p for p in parts if p not in LEAGUE_METADATA]
    if unknown:
        raise SystemExit(
            f"Unknown league short_code(s): {unknown}.  "
            f"Known: {sorted(LEAGUE_METADATA)} or 'all'."
        )
    return parts


# ─────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--league", default="EN_PREM",
        help="League short_code, comma-separated list, or 'all' (default: EN_PREM).",
    )
    parser.add_argument(
        "--through-season", default=_current_season_string(),
        help="Last season to ingest (default = current season, e.g. '2025-26').",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Force re-fetch of every season CSV (default: use cached files).",
    )
    parser.add_argument(
        "--season", default=None,
        help="Single season override (e.g. '2023-24'); skips the range loop.",
    )
    args = parser.parse_args(argv[1:])

    assert_data_root_ready()
    console = Console()
    leagues = _parse_leagues(args.league)
    console.log(
        f"Ingesting leagues: {', '.join(leagues)}  "
        f"(through {args.through_season})"
    )

    table = Table(
        title=f"English-leagues ingestion through {args.through_season}",
        show_lines=False,
    )
    table.add_column("League")
    table.add_column("Season")
    table.add_column("Parsed", justify="right")
    table.add_column("Inserted", justify="right")
    table.add_column("Skipped", justify="right")
    table.add_column("Cache?", justify="center")

    with connect() as conn:
        applied = _bootstrap_schema_if_empty(conn)
        if applied:
            console.log("[yellow]bootstrapped empty database from db/schema.sql[/yellow]")
        stadium_name_to_id, country_id = load_stadia(conn)
        alias_to_club_id, periods = load_club_stadium_history(
            conn, stadium_name_to_id,
        )
        resolver = build_stadium_resolver(periods, stadium_name_to_id)

        for league_code in leagues:
            league_id = _ensure_league(conn, league_code, country_id)
            seasons = (
                [args.season]
                if args.season is not None
                else all_seasons_for(league_code, args.through_season)
            )
            for season in seasons:
                try:
                    stats = ingest_season(
                        conn,
                        league_short_code=league_code,
                        season=season,
                        country_id=country_id,
                        league_id=league_id,
                        alias_to_club_id=alias_to_club_id,
                        stadium_resolver=resolver,
                        use_cache=not args.no_cache,
                    )
                except Exception as exc:
                    console.log(
                        f"[red]error on {league_code} {season}:[/red] "
                        f"{type(exc).__name__}: {exc}"
                    )
                    table.add_row(league_code, season, "—", "—", "—", "err")
                    continue
                table.add_row(
                    league_code, stats.season,
                    str(stats.parsed_rows),
                    str(stats.inserted_rows),
                    str(stats.skipped_rows),
                    "✓" if stats.from_cache else "↓",
                )

    console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
