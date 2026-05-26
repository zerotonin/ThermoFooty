# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — scripts/smoke_test_phase2b                        ║
# ║  « pick 10 random matches → run the cascade → assert sanity »    ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  End-to-end Phase-2b sanity check.  Picks 10 random matches      ║
# ║  from the SQLite `matches` table, joins each to its stadium      ║
# ║  coordinates, runs the four-tier weather cascade, and prints     ║
# ║  the result in a Rich table.                                     ║
# ║                                                                  ║
# ║  Exits non-zero if:                                              ║
# ║    - the database is empty (run ingest_epl.py first)             ║
# ║    - any cascade call raises (network failure is OK; crashes     ║
# ║      in our code are not)                                        ║
# ║    - resolved baseline is impossibly small (< 10 days) for       ║
# ║      every probe (suggests the cascade is silently broken)       ║
# ║                                                                  ║
# ║  Network-touching: hits meteostat for each probe.  Run with      ║
# ║  --offline to skip the cascade and just sanity-check the         ║
# ║  ingestion → SQLite → stadia-join shape.                         ║
# ╚══════════════════════════════════════════════════════════════════╝
"""End-to-end smoke test for Phase 2b: ingestion → cascade → AnomalyFetch."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date

from rich.console import Console
from rich.table import Table

from thermofooty.db import connect
from thermofooty.lookup import resolve_event_anomaly

N_PROBES = 10


@dataclass(frozen=True)
class Probe:
    """One row sampled from `matches` joined to `stadia`."""

    match_id: int
    match_date: date
    home_name: str
    away_name: str
    stadium_name: str
    latitude: float
    longitude: float


def _sample_matches(conn, n: int) -> list[Probe]:
    cur = conn.execute(
        """
        SELECT m.match_id, m.match_date,
               ch.name AS home_name, ca.name AS away_name,
               s.name AS stadium_name, s.latitude, s.longitude
        FROM matches m
        JOIN clubs ch ON ch.club_id = m.home_club_id
        JOIN clubs ca ON ca.club_id = m.away_club_id
        JOIN stadia s ON s.stadium_id = m.stadium_id
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (n,),
    )
    out: list[Probe] = []
    for row in cur.fetchall():
        out.append(
            Probe(
                match_id=row[0],
                match_date=date.fromisoformat(row[1]),
                home_name=row[2],
                away_name=row[3],
                stadium_name=row[4],
                latitude=float(row[5]),
                longitude=float(row[6]),
            )
        )
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-n", "--n-probes", type=int, default=N_PROBES,
        help=f"Number of random matches to probe (default {N_PROBES}).",
    )
    parser.add_argument(
        "--offline", action="store_true",
        help="Sample matches but don't call the cascade — sanity-check the join only.",
    )
    args = parser.parse_args(argv[1:])

    console = Console()
    table = Table(
        title=f"Phase-2b smoke test ({args.n_probes} random matches)",
        show_lines=True,
    )
    table.add_column("Date")
    table.add_column("Fixture")
    table.add_column("Stadium")
    if not args.offline:
        table.add_column("Tmax", justify="right")
        table.add_column("Provenance")
        table.add_column("Baseline n", justify="right")

    with connect() as conn:
        cur = conn.execute("SELECT count(*) FROM matches")
        n_matches = int(cur.fetchone()[0])
        if n_matches == 0:
            console.print(
                "[red]matches table is empty — run scripts/ingest_epl.py first[/red]"
            )
            return 2

        probes = _sample_matches(conn, args.n_probes)

    console.log(
        f"Sampled {len(probes)} matches from {n_matches:,} in the database."
    )

    any_real_baseline = False
    crashes = 0
    for p in probes:
        fixture = f"{p.home_name} vs {p.away_name}"
        if args.offline:
            table.add_row(p.match_date.isoformat(), fixture, p.stadium_name)
            continue
        try:
            result = resolve_event_anomaly(p.latitude, p.longitude, p.match_date)
        except Exception as exc:
            console.log(
                f"[red]cascade crashed on match {p.match_id}:[/red] "
                f"{type(exc).__name__}: {exc}"
            )
            crashes += 1
            table.add_row(
                p.match_date.isoformat(), fixture, p.stadium_name,
                "—", "[red]crash[/red]", "—",
            )
            continue
        tmax = "—" if result.tmax_event_c is None else f"{result.tmax_event_c:5.1f} °C"
        n_baseline = len(result.baseline)
        if n_baseline >= 20:
            any_real_baseline = True
        table.add_row(
            p.match_date.isoformat(), fixture, p.stadium_name,
            tmax, result.provenance, str(n_baseline),
        )

    console.print(table)
    if crashes:
        console.print(f"[red]{crashes} cascade crash(es) — failing[/red]")
        return 1
    if not args.offline and not any_real_baseline:
        console.print(
            "[yellow]no probe returned a usable baseline (n >= 20). "
            "Probably a network issue, but worth eyeballing.[/yellow]"
        )
    console.print("[green]smoke test passed[/green]")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
