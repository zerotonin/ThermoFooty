# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — weather/twentycr_src                              ║
# ║  « Tier-4 daily Tmax via NOAA 20CRv3 reanalysis (1806-1980) »    ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Vendored from ThermoStrife v0.1.1                               ║
# ║  Source: https://doi.org/10.5281/zenodo.20371612                 ║
# ║  Sync date: 2026-05-26                                           ║
# ║  Behavioural change vs source: cache path re-rooted via          ║
# ║  thermofooty.config.CACHE_TWENTYCR so the per-(cell, year)       ║
# ║  parquet cache lands on the developer-configured data root;      ║
# ║  all algorithmic code byte-identical to upstream.                ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  20th Century Reanalysis V3 ensemble mean, 1° global grid,       ║
# ║  daily 2m air Tmax in Kelvin.  Accessed via NOAA PSL THREDDS     ║
# ║  OPeNDAP and cached per (lat_cell, lon_cell, year) as parquet    ║
# ║  so repeated baseline queries on the same grid cell hit one      ║
# ║  network round-trip per year instead of one per day.             ║
# ║                                                                  ║
# ║  Compo et al. 2011 / Slivinski et al. 2019.  Free public data.   ║
# ║  In ThermoFooty this tier is only used for pre-1981              ║
# ║  tournament-panel matches (pre-1970 league panel is out of       ║
# ║  scope per the OSF pre-registration).                            ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Tier 4: daily Tmax via NOAA 20CRv3 reanalysis (vendored from ThermoStrife v0.1.1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from thermofooty.config import CACHE_TWENTYCR

# ─────────────────────────────────────────────────────────────────
#  Coverage + endpoint
# ─────────────────────────────────────────────────────────────────

FIRST_YEAR: int = 1806
LAST_YEAR: int = 1980  # ERA5 takes over from 1981; 20CRv3 ends here

OPENDAP_TEMPLATE: str = (
    "https://psl.noaa.gov/thredds/dodsC/"
    "Datasets/20thC_ReanV3/Dailies/2mSI/tmax.2m.{year}.nc"
)

TWENTYCR_CACHE: Path = CACHE_TWENTYCR


def covers(when: date) -> bool:
    """True if ``when`` falls inside the 20CRv3 coverage window."""
    return FIRST_YEAR <= when.year <= LAST_YEAR


# ─────────────────────────────────────────────────────────────────
#  Grid helpers
# ─────────────────────────────────────────────────────────────────


def _to_360(lon: float) -> float:
    """Convert (-180, 180] longitude to [0, 360) used by 20CRv3."""
    return lon if lon >= 0 else lon + 360.0


def _grid_cell(lat: float, lon: float) -> tuple[int, int]:
    """Nearest integer-degree grid cell for the cache key."""
    return int(round(lat)), int(round(_to_360(lon)))


def _cache_path(lat_cell: int, lon_cell: int, year: int) -> Path:
    return TWENTYCR_CACHE / f"{lat_cell:+03d}_{lon_cell:03d}" / f"{year:04d}.parquet"


def _load_or_fetch_year(lat: float, lon: float, year: int) -> pd.Series:
    """Daily Tmax (°C) for one year at the grid cell nearest (lat, lon).

    Returns a Series indexed by ``datetime.date``.  Empty Series if the
    year is outside coverage or the OPeNDAP fetch fails.
    """
    if not FIRST_YEAR <= year <= LAST_YEAR:
        return pd.Series(dtype="float64")

    lat_cell, lon_cell = _grid_cell(lat, lon)
    path = _cache_path(lat_cell, lon_cell, year)
    if path.exists():
        df = pd.read_parquet(path)
        return pd.Series(df["tmax"].values, index=df.index, dtype="float64")

    import xarray as xr

    url = OPENDAP_TEMPLATE.format(year=year)
    try:
        ds = xr.open_dataset(url, decode_times=True)
        cell = ds.tmax.sel(
            lat=lat_cell, lon=lon_cell, method="nearest"
        ).load()
        ds.close()
    except Exception as exc:  # pragma: no cover - network failures vary
        print(f"[twentycr] OPeNDAP fetch failed for {year}: {exc}")
        return pd.Series(dtype="float64")

    # Coordinates: time is datetime64; convert to date.  Values are Kelvin.
    times = pd.to_datetime(cell.time.values).date
    values_c = cell.values.astype("float64") - 273.15
    series = pd.Series(values_c, index=pd.Index(times, name="date"))

    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"tmax": series.values}, index=series.index).to_parquet(path)
    return series


# ─────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AnomalyFetch:
    """20CRv3 event-day + baseline-window result."""

    tmax_event_c: float | None
    baseline: pd.DataFrame
    station_id: str
    provenance: str
    note: str = ""


def fetch_daily_tmax(lat: float, lon: float, when: date) -> float | None:
    """Return daily Tmax in °C for ``when`` at the cell nearest (lat, lon)."""
    if not covers(when):
        return None
    series = _load_or_fetch_year(lat, lon, when.year)
    if when in series.index:
        v = series.loc[when]
        return float(v) if pd.notna(v) else None
    return None


def fetch_baseline_window(
    lat: float,
    lon: float,
    when: date,
    *,
    half_window_years: int = 5,
    event_buffer_days: int = 7,
) -> pd.DataFrame:
    """Same-month ±N-year daily Tmax values, excluding event ± buffer."""
    target_month = when.month
    years = range(when.year - half_window_years, when.year + half_window_years + 1)
    buf = timedelta(days=event_buffer_days)
    event_lo, event_hi = when - buf, when + buf

    frames = []
    for year in years:
        if not FIRST_YEAR <= year <= LAST_YEAR:
            continue
        series = _load_or_fetch_year(lat, lon, year)
        if series.empty:
            continue
        # Filter to the target calendar month and drop the event window
        idx = pd.Index([d.month for d in series.index])
        sub = series.loc[(idx == target_month).tolist()]
        sub = sub.dropna()
        if year == when.year:
            sub = sub[(sub.index < event_lo) | (sub.index > event_hi)]
        if not sub.empty:
            frames.append(pd.DataFrame({"tmax": sub.values}, index=sub.index))
    if not frames:
        return pd.DataFrame(columns=["tmax"])
    return pd.concat(frames)


def resolve_for_anomaly(
    lat: float,
    lon: float,
    when: date,
    *,
    half_window_years: int = 5,
    event_buffer_days: int = 7,
    min_baseline_days: int = 20,
) -> AnomalyFetch:
    """Resolve event-day and matching baseline window via 20CRv3."""
    if not covers(when):
        return AnomalyFetch(
            tmax_event_c=None,
            baseline=pd.DataFrame(columns=["tmax"]),
            station_id="",
            provenance="",
            note=f"{when.year} outside 20CRv3 coverage ({FIRST_YEAR}-{LAST_YEAR})",
        )

    tmax = fetch_daily_tmax(lat, lon, when)
    if tmax is None:
        return AnomalyFetch(
            tmax_event_c=None,
            baseline=pd.DataFrame(columns=["tmax"]),
            station_id="20CRv3",
            provenance="",
            note=f"20CRv3 has no value for {when} at cell {_grid_cell(lat, lon)}",
        )
    baseline = fetch_baseline_window(
        lat, lon, when,
        half_window_years=half_window_years,
        event_buffer_days=event_buffer_days,
    )
    if len(baseline) < min_baseline_days:
        return AnomalyFetch(
            tmax_event_c=None,
            baseline=baseline,
            station_id="20CRv3",
            provenance="",
            note=(
                f"20CRv3 baseline only {len(baseline)} days "
                f"(< {min_baseline_days})"
            ),
        )
    lat_cell, lon_cell = _grid_cell(lat, lon)
    return AnomalyFetch(
        tmax_event_c=tmax,
        baseline=baseline,
        station_id=f"20CRv3_{lat_cell:+03d}_{lon_cell:03d}",
        provenance="tier4_20crv3",
        note=(
            f"20CRv3 cell ({lat_cell}°, {lon_cell}°E); "
            f"baseline n={len(baseline)} same-month Tmax values"
        ),
    )
