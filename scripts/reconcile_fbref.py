# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — scripts/reconcile_fbref                           ║
# ║  « Phase 3d: confirm fbref event-sum ≈ football-data aggregate » ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Reads the SQLite database, joins per-match card aggregates      ║
# ║  from football-data.co.uk against fbref's event-sum, and reports ║
# ║  per-(league, season) the fraction within tolerance plus any     ║
# ║  matches whose delta exceeds the threshold.                      ║
# ║                                                                  ║
# ║  No network use; pure SQL aggregation against the local DB.      ║
# ╚══════════════════════════════════════════════════════════════════╝
"""fbref vs football-data.co.uk reconciliation report."""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.table import Table

from thermofooty.config import assert_data_root_ready
from thermofooty.db import connect
from thermofooty.sources.fbref_reconcile import (
    by_league_season,
    per_match_mismatches,
)


def _print_league_season_table(console, rows, tolerance: int) -> None:
    table = Table(
        title=f"fbref vs football-data.co.uk reconciliation (tolerance ±{tolerance})",
        show_lines=False,
    )
    table.add_column("League")
    table.add_column("Season")
    table.add_column("Matches", justify="right")
    table.add_column("Exact", justify="right")
    table.add_column("Within", justify="right")
    table.add_column("Over", justify="right")
    table.add_column("Mean |Δhome|", justify="right")
    table.add_column("Mean |Δaway|", justify="right")
    for r in rows:
        within_pct = r.n_within_tolerance / r.n_matches_with_both_sources
        style = "red" if within_pct < 0.90 else "yellow" if within_pct < 0.99 else ""
        table.add_row(
            r.league_short_code, r.season,
            f"{r.n_matches_with_both_sources:,}",
            f"{r.n_perfect_match:,}",
            f"{r.n_within_tolerance:,}",
            f"{r.n_over_tolerance:,}",
            f"{r.mean_abs_delta_home:.3f}",
            f"{r.mean_abs_delta_away:.3f}",
            style=style or None,
        )
    console.print(table)


def _print_mismatches_table(console, mismatches, *, limit: int) -> None:
    if not mismatches:
        console.print("[green]no per-match mismatches exceed the tolerance[/green]")
        return
    table = Table(
        title=f"Per-match mismatches (showing first {min(limit, len(mismatches))}"
              f" of {len(mismatches):,})",
        show_lines=False,
    )
    table.add_column("Date")
    table.add_column("League")
    table.add_column("Fixture")
    table.add_column("FD home", justify="right")
    table.add_column("fbref home", justify="right")
    table.add_column("Δhome", justify="right")
    table.add_column("FD away", justify="right")
    table.add_column("fbref away", justify="right")
    table.add_column("Δaway", justify="right")
    for m in mismatches[:limit]:
        fixture = f"{m.home_name} vs {m.away_name}"
        table.add_row(
            m.match_date, m.league_short_code, fixture,
            str(m.fd_card_home), str(m.fbref_card_home),
            f"{m.delta_home:+d}",
            str(m.fd_card_away), str(m.fbref_card_away),
            f"{m.delta_away:+d}",
        )
    console.print(table)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tolerance", type=int, default=1,
        help="Max |fbref − football-data| per side to accept (default 1).",
    )
    parser.add_argument(
        "--max-mismatches-shown", type=int, default=20,
        help="Cap on the per-match mismatches table (default 20).",
    )
    args = parser.parse_args(argv[1:])

    assert_data_root_ready()
    console = Console()
    with connect() as conn:
        cur = conn.execute("SELECT count(*) FROM cards WHERE source = 'fbref'")
        n_fbref = int(cur.fetchone()[0])
        if n_fbref == 0:
            console.print(
                "[red]no fbref cards in the database — run scripts/ingest_fbref.py first[/red]"
            )
            return 2
        per_season = by_league_season(conn, tolerance=args.tolerance)
        mismatches = per_match_mismatches(conn, tolerance=args.tolerance)

    _print_league_season_table(console, per_season, args.tolerance)
    _print_mismatches_table(console, mismatches, limit=args.max_mismatches_shown)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
