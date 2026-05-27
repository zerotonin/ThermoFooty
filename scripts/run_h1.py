# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — scripts/run_h1                                    ║
# ║  « Phase 5a: exploratory H1 case-crossover on the card proxy »   ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Materialises the analysis_panel from SQLite, builds the per-    ║
# ║  (team, year-month) case-crossover events, fits the conditional  ║
# ║  logit, and prints a Rich-table summary of the headline OR + CI  ║
# ║  + p-value.  Also writes a CSV companion under derived/ so the   ║
# ║  numbers are reproducible without re-running the fit.            ║
# ║                                                                  ║
# ║  Scope: the football-data.co.uk ingestion gives match-level      ║
# ║  card aggregates only — no per-card reason codes — so this run   ║
# ║  uses the side_received_card proxy (≥1 card on this side).  The  ║
# ║  OSF-locked confirmatory test on red-cards-for-violent-conduct   ║
# ║  lands in Phase 5b alongside the fbref ingestion.                ║
# ║                                                                  ║
# ║  No network use; reads SQLite + rerandomstats only.              ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Exploratory H1 case-crossover on the side-received-card proxy."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from thermofooty.config import DERIVED_DIR, assert_data_root_ready
from thermofooty.db import connect
from thermofooty.inference import run_h1
from thermofooty.panel import materialise_analysis_panel


def _print_result_table(console: Console, result: dict) -> None:
    table = Table(
        title="Phase 5a — H1 exploratory fit (case-crossover conditional logit)",
        show_lines=False,
    )
    table.add_column("Quantity")
    table.add_column("Value", justify="right")
    if result.get("skipped"):
        table.add_row("Status", "[yellow]skipped[/yellow]")
        table.add_row("Reason", str(result.get("reason", "—")))
        table.add_row("n_events", str(result.get("n_events", 0)))
        console.print(table)
        return
    ci_lo, ci_hi = result["or_ci95"]
    table.add_row("Outcome (proxy)", result["proxy_outcome"])
    table.add_row("Events built", f"{result['n_events']:,}")
    table.add_row("Events in fit", f"{result['n_events_in_fit']:,}")
    table.add_row("Rows in fit", f"{result['n_rows_in_fit']:,}")
    table.add_row("OR per +1 °C anomaly", f"{result['or_per_degree']:.4f}")
    table.add_row("95% CI", f"[{ci_lo:.4f}, {ci_hi:.4f}]")
    table.add_row("β  (per °C)", f"{result['beta']:+.4f}")
    table.add_row("SE (per °C)", f"{result['se']:.4f}")
    table.add_row("p (two-sided)", f"{result['pvalue_two_sided']:.4g}")
    table.add_row(
        "p (one-sided, β > 0)",
        f"{result['pvalue_one_sided_pos']:.4g}  ← OSF-locked direction",
    )
    if result.get("covariate_betas"):
        for name, b in result["covariate_betas"].items():
            table.add_row(f"covariate β [{name}]", f"{b:+.4f}")
    console.print(table)


def _write_result_csv(result: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, str]] = [
        ("run_at_utc", datetime.now(UTC).isoformat(timespec="seconds")),
        ("proxy_outcome", str(result.get("proxy_outcome", ""))),
        ("status", "skipped" if result.get("skipped") else "fit"),
    ]
    if not result.get("skipped"):
        ci_lo, ci_hi = result["or_ci95"]
        rows.extend([
            ("n_events", str(result["n_events"])),
            ("n_events_in_fit", str(result["n_events_in_fit"])),
            ("n_rows_in_fit", str(result["n_rows_in_fit"])),
            ("or_per_degree", f"{result['or_per_degree']:.6f}"),
            ("or_ci95_low", f"{ci_lo:.6f}"),
            ("or_ci95_high", f"{ci_hi:.6f}"),
            ("beta_per_degree", f"{result['beta']:.6f}"),
            ("se_per_degree", f"{result['se']:.6f}"),
            ("pvalue_two_sided", f"{result['pvalue_two_sided']:.8g}"),
            ("pvalue_one_sided_pos", f"{result['pvalue_one_sided_pos']:.8g}"),
        ])
        for name, b in (result.get("covariate_betas") or {}).items():
            rows.append((f"covariate_beta__{name}", f"{b:.6f}"))
    else:
        rows.append(("reason", str(result.get("reason", ""))))

    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["key", "value"])
        for key, value in rows:
            writer.writerow([key, value])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--outcome", default="side_received_card",
        help=(
            "Binary outcome column.  Default 'side_received_card' is the "
            "Phase-5a proxy; swap to 'side_received_red' after Phase 3 "
            "fbref ingestion populates per-card reason codes."
        ),
    )
    parser.add_argument(
        "--write-panel-parquet", action="store_true",
        help="Persist the analysis_panel to derived/analysis_panel.parquet.",
    )
    parser.add_argument(
        "--output-csv", default=None,
        help=(
            "Output path for the H1 result CSV.  Default: "
            "$DERIVED_DIR/phase5a_h1_result.csv"
        ),
    )
    args = parser.parse_args(argv[1:])

    assert_data_root_ready()
    console = Console()

    with connect() as conn:
        cur = conn.execute("SELECT count(*) FROM weather")
        n_weather = int(cur.fetchone()[0])
        if n_weather == 0:
            console.print(
                "[red]weather table is empty — run scripts/backfill_weather.py first[/red]"
            )
            return 2
        console.log(f"Materialising analysis_panel ({n_weather:,} weather rows in db) …")
        panel = materialise_analysis_panel(
            conn, write_parquet=args.write_panel_parquet,
        )

    console.log(
        f"Panel built: {len(panel):,} match-side rows across "
        f"{panel['league_short_code'].nunique()} league(s)."
    )

    console.log("Fitting case-crossover conditional logit …")
    result = run_h1(panel, outcome_col=args.outcome)
    _print_result_table(console, result)

    out_path = Path(args.output_csv) if args.output_csv else (
        DERIVED_DIR / "phase5a_h1_result.csv"
    )
    _write_result_csv(result, out_path)
    console.log(f"Result written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
