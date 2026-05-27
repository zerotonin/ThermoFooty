# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — scripts/ingest_fbref                              ║
# ║  « Phase 3c: walk every (league, season) schedule, ingest        ║
# ║    every match-report, upsert lineups + cards + players »        ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Network-heavy.  Default rate-limit is 3 s per request; a full   ║
# ║  EPL season is ~380 matches × 2 fetches (schedule + report) so   ║
# ║  one season is ~38 min on a cold cache.  All HTML is cached      ║
# ║  under $THERMOFOOTY_DATA_ROOT/raw/fbref_html/ so re-runs are     ║
# ║  effectively instant (cache lookup + reparse only).              ║
# ║                                                                  ║
# ║  --league flag accepts EN_PREM / EN_CHAMP / EN_L1 / EN_L2 / all  ║
# ║  / a comma-separated list, identical to ingest_english_leagues. ║
# ║  --through-season caps the iteration end; default = current      ║
# ║  season.                                                         ║
# ║                                                                  ║
# ║  Idempotent: re-runs skip already-fetched HTML, and the upserts  ║
# ║  use the players/lineups UNIQUE constraints.  Per-match cards    ║
# ║  are wiped + re-inserted on each pass so improved aggression-    ║
# ║  set classification propagates cleanly.                          ║
# ╚══════════════════════════════════════════════════════════════════╝
"""CLI entry point for fbref lineups + cards ingestion."""

from __future__ import annotations

import argparse
import sys
from datetime import date

from rich.console import Console
from rich.table import Table

from thermofooty.config import assert_data_root_ready
from thermofooty.db import connect
from thermofooty.sources.fbref import RateLimitedClient
from thermofooty.sources.fbref_ingest import (
    ingest_one_season,
    record_provenance,
)
from thermofooty.sources.football_data_uk import (
    LEAGUE_METADATA,
    all_seasons_for,
)
from thermofooty.sources.stadia import (
    load_club_stadium_history,
    load_stadia,
)


def _current_season_string() -> str:
    today = date.today()
    start_year = today.year if today.month >= 8 else today.year - 1
    end_two = (start_year + 1) % 100
    return f"{start_year}-{end_two:02d}"


def _parse_leagues(value: str) -> list[str]:
    if value == "all":
        return list(LEAGUE_METADATA)
    parts = [v.strip() for v in value.split(",") if v.strip()]
    unknown = [p for p in parts if p not in LEAGUE_METADATA]
    if unknown:
        raise SystemExit(
            f"unknown league short_code(s): {unknown}.  "
            f"known: {sorted(LEAGUE_METADATA)} or 'all'."
        )
    return parts


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--league", default="EN_PREM",
        help="League short_code, comma-separated list, or 'all' (default EN_PREM).",
    )
    parser.add_argument(
        "--through-season", default=_current_season_string(),
        help="Last season to ingest (default = current season).",
    )
    parser.add_argument(
        "--season", default=None,
        help="Single season override (e.g. '2023-24'); skips the range loop.",
    )
    parser.add_argument(
        "--rate-limit-s", type=float, default=3.0,
        help="Min seconds between fbref requests (default 3 — be polite).",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Force re-fetch of every HTML page (default: use cached files).",
    )
    args = parser.parse_args(argv[1:])

    assert_data_root_ready()
    console = Console()
    leagues = _parse_leagues(args.league)
    client = RateLimitedClient(min_interval_s=args.rate_limit_s)

    table = Table(
        title=f"fbref ingest — leagues={','.join(leagues)} through {args.through_season}",
        show_lines=False,
    )
    table.add_column("League")
    table.add_column("Season")
    table.add_column("Scheduled", justify="right")
    table.add_column("Resolved", justify="right")
    table.add_column("Upserted", justify="right")
    table.add_column("Skipped", justify="right")
    table.add_column("Failed", justify="right")

    with connect() as conn:
        stadium_lookup, _country_id = load_stadia(conn)
        alias_to_club_id, _ = load_club_stadium_history(conn, stadium_lookup)

        for league_code in leagues:
            seasons = (
                [args.season] if args.season is not None
                else all_seasons_for(league_code, args.through_season)
            )
            for season in seasons:
                console.log(
                    f"Ingesting {league_code} {season} "
                    f"(rate-limit {args.rate_limit_s}s, cache "
                    f"{'OFF' if args.no_cache else 'ON'}) …"
                )
                try:
                    stats = ingest_one_season(
                        conn,
                        league_short_code=league_code,
                        season=season,
                        alias_to_club_id=alias_to_club_id,
                        client=client,
                        refetch=args.no_cache,
                    )
                except Exception as exc:
                    console.log(
                        f"[red]error on {league_code} {season}:[/red] "
                        f"{type(exc).__name__}: {exc}"
                    )
                    table.add_row(league_code, season, "—", "—", "—", "—", "err")
                    continue
                record_provenance(conn, stats)
                table.add_row(
                    league_code, stats.season,
                    str(stats.n_scheduled),
                    str(stats.n_resolved),
                    str(stats.n_upserted),
                    str(stats.n_skipped_unresolved),
                    str(stats.n_failed_fetch),
                )

    console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
