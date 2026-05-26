# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — scripts/backfill_weather                          ║
# ║  « Phase 4: cascade-driven weather backfill across all matches » ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Iterates every (stadium, match_date) pair in the matches table  ║
# ║  that doesn't yet have a weather row, calls the four-tier        ║
# ║  weather cascade, and writes the resolved (Tmax, baseline mean,  ║
# ║  baseline std, anomaly, provenance, station_id) row into         ║
# ║  weather.                                                        ║
# ║                                                                  ║
# ║  Resumable: a Ctrl-C or crash mid-run loses at most the          ║
# ║  current --commit-every batch.  Re-invoke and it picks up where  ║
# ║  it stopped (LEFT JOIN on the weather table filters resolved     ║
# ║  rows from the work queue).                                      ║
# ║                                                                  ║
# ║  Network-touching: hits meteostat for every stadium-day in the   ║
# ║  British Isles bbox.  Cache lives under                          ║
# ║  $THERMOFOOTY_DATA_ROOT/cache/meteostat/ so subsequent re-runs   ║
# ║  for the same (station, year-month) pairs are local.             ║
# ╚══════════════════════════════════════════════════════════════════╝
"""CLI entry point for the cascade-driven weather backfill."""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from thermofooty.config import assert_data_root_ready
from thermofooty.db import connect
from thermofooty.weather.backfill import (
    BackfillStats,
    backfill_iter,
    coverage_by_league_season,
    coverage_by_tier,
    record_provenance,
    select_pending_probes,
)


def _print_tier_table(console: Console, conn) -> None:
    table = Table(title="Weather coverage by source tier", show_lines=False)
    table.add_column("Tier")
    table.add_column("Rows", justify="right")
    table.add_column("Share", justify="right")
    for tc in coverage_by_tier(conn):
        table.add_row(
            tc.source_tier, f"{tc.n_rows:,}", f"{tc.fraction:6.1%}",
        )
    console.print(table)


def _print_league_season_table(console: Console, conn, *, min_unresolved: int = 1) -> None:
    table = Table(
        title="Coverage by league × season  (rows shown: any unresolved)",
        show_lines=False,
    )
    table.add_column("League")
    table.add_column("Season")
    table.add_column("Matches", justify="right")
    table.add_column("Resolved", justify="right")
    table.add_column("Unverifiable", justify="right")
    table.add_column("Coverage", justify="right")
    any_shown = False
    for cov in coverage_by_league_season(conn):
        if (cov.n_matches - cov.n_resolved) < min_unresolved:
            continue
        any_shown = True
        coverage_pct = f"{cov.fraction_resolved:6.1%}"
        if cov.fraction_resolved < 0.5:
            style = "red"
        elif cov.fraction_resolved < 0.9:
            style = "yellow"
        else:
            style = ""
        table.add_row(
            cov.league_short_code, cov.season,
            f"{cov.n_matches:,}", f"{cov.n_resolved:,}",
            f"{cov.n_unverifiable:,}", coverage_pct,
            style=style or None,
        )
    if any_shown:
        console.print(table)
    else:
        console.print("[green]every (league, season) cell at 100% coverage[/green]")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit-every", type=int, default=50,
        help="Commit to SQLite every N probes (default 50).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Stop after N probes (useful for partial smoke runs).",
    )
    parser.add_argument(
        "--report-only", action="store_true",
        help="Skip the cascade; just print the coverage tables from the current weather table.",
    )
    args = parser.parse_args(argv[1:])

    assert_data_root_ready()
    console = Console()

    with connect() as conn:
        if args.report_only:
            _print_tier_table(console, conn)
            _print_league_season_table(console, conn)
            return 0

        probes = select_pending_probes(conn)
        if args.limit is not None:
            probes = probes[: args.limit]
        n = len(probes)
        if n == 0:
            console.print("[green]nothing to do — every match already has a weather row[/green]")
            _print_tier_table(console, conn)
            return 0

        console.log(f"Resolving {n:,} pending (stadium, date) probes …")

        by_tier: dict[str, int] = {}
        excluded = 0
        unverifiable = 0
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            task = progress.add_task("backfill", total=n)
            for row in backfill_iter(conn, probes, commit_every=args.commit_every):
                by_tier[row.source_tier] = by_tier.get(row.source_tier, 0) + 1
                if row.source_tier == "excluded_altitude":
                    excluded += 1
                elif row.source_tier == "unverifiable":
                    unverifiable += 1
                progress.update(task, advance=1)

        stats = BackfillStats(
            probed=n,
            inserted=n,
            skipped_already_present=0,
            excluded_altitude=excluded,
            unverifiable=unverifiable,
            by_tier=by_tier,
        )
        record_provenance(conn, stats)
        console.log(
            f"[green]done[/green] — inserted {stats.inserted:,} rows; "
            f"excluded_altitude={stats.excluded_altitude}, "
            f"unverifiable={stats.unverifiable}"
        )
        _print_tier_table(console, conn)
        _print_league_season_table(console, conn)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
