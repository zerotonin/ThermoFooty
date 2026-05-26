# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — scripts/validate_cascade                          ║
# ║  « smoke-test the four-tier weather cascade end-to-end »         ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Picks a handful of canonical (lat, lon, date) probes that each  ║
# ║  exercise a different tier of the cascade and prints the result  ║
# ║  in a small Rich table.  Use this whenever upstream APIs may     ║
# ║  have shifted, or before kicking off a large ingestion run.      ║
# ║                                                                  ║
# ║  Probes are intentionally biased towards summer / sensible       ║
# ║  events so a returned None signals upstream breakage rather      ║
# ║  than missing data.                                              ║
# ║                                                                  ║
# ║  Hits the network for tier 1 (meteostat).  Tier 3 (ERA5) only    ║
# ║  fires if ~/.cdsapirc is present; tier 4 (20CRv3) needs xarray   ║
# ║  + netCDF4 to be importable.                                     ║
# ╚══════════════════════════════════════════════════════════════════╝
"""End-to-end smoke test of the ThermoFooty weather cascade."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date

from rich.console import Console
from rich.table import Table

from thermofooty.lookup import resolve_event_anomaly

# ─────────────────────────────────────────────────────────────────
#  Probe set  « one event per tier, plus one designed to miss »
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Probe:
    label: str
    lat: float
    lon: float
    when: date
    expected_tier: str


PROBES: list[Probe] = [
    Probe(
        label="De Bilt heatwave 2022",
        lat=52.10, lon=5.18, when=date(2022, 7, 19),
        expected_tier="tier1_ghcn",
    ),
    Probe(
        label="Manchester Old Trafford 2018",
        lat=53.46, lon=-2.29, when=date(2018, 6, 14),
        # GHCN coverage in NW England is dense; tier1 should win.  HadCET
        # is the fallback if meteostat declines, which the cascade will
        # surface in the provenance column.
        expected_tier="tier1_ghcn|tier2_hadcet_max",
    ),
    Probe(
        label="Madrid Bernabéu summer 2022",
        lat=40.45, lon=-3.69, when=date(2022, 7, 5),
        expected_tier="tier1_ghcn|tier3_era5",
    ),
    Probe(
        label="Bern WM-Endspiel 1954 (pre-ERA5)",
        lat=46.96, lon=7.46, when=date(1954, 7, 4),
        # 1954 predates ERA5's 1981 floor; meteostat may still have it
        # via DWD/ECA&D, otherwise 20CRv3 takes the call.
        expected_tier="tier1_ghcn|tier4_20crv3",
    ),
]


# ─────────────────────────────────────────────────────────────────
#  Runner
# ─────────────────────────────────────────────────────────────────


def _format_tmax(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:5.1f} °C"


def _tier_matches(actual: str, expected: str) -> bool:
    return actual in expected.split("|")


def main(argv: list[str]) -> int:
    console = Console()
    table = Table(
        title="ThermoFooty cascade smoke test",
        caption="Tier-1 needs network; tier-3 needs ~/.cdsapirc; tier-4 needs xarray + netCDF4.",
        show_lines=True,
    )
    table.add_column("Probe", style="bold")
    table.add_column("Date")
    table.add_column("Tmax")
    table.add_column("Provenance")
    table.add_column("Station / cell")
    table.add_column("Match?", justify="center")

    all_ok = True
    for probe in PROBES:
        try:
            result = resolve_event_anomaly(probe.lat, probe.lon, probe.when)
        except Exception as exc:  # surface upstream breakage cleanly
            console.log(f"[red]error on {probe.label}:[/red] {type(exc).__name__}: {exc}")
            all_ok = False
            continue

        match = _tier_matches(result.provenance, probe.expected_tier)
        all_ok &= match
        table.add_row(
            probe.label,
            probe.when.isoformat(),
            _format_tmax(result.tmax_event_c),
            result.provenance,
            result.station_id or "—",
            "[green]ok[/green]" if match else f"[yellow]want {probe.expected_tier}[/yellow]",
        )

    console.print(table)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
