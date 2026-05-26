# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — weather/era5_src                                  ║
# ║  « Tier-3 daily Tmax via ECMWF ERA5 reanalysis (1981+) »         ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Vendored from ThermoStrife v0.1.1                               ║
# ║  Source: https://doi.org/10.5281/zenodo.20371612                 ║
# ║  Sync date: 2026-05-26                                           ║
# ║  Behavioural change vs source: cache path re-rooted via          ║
# ║  thermofooty.config.CACHE_ERA5 so the parquet cache lands on     ║
# ║  the developer-configured data root; all algorithmic code        ║
# ║  byte-identical to the upstream ThermoStrife release.            ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  ERA5 single-level 2-m air temperature on a 0.25° global grid,   ║
# ║  fetched via the Copernicus CDS API.  Hourly natively; daily     ║
# ║  Tmax is derived per-day from the 24 hourly values.              ║
# ║                                                                  ║
# ║  Coverage is deliberately limited to 1981+: the 1940-1980 window ║
# ║  is owned by 20CRv3 (Tier 4), which has substantial observa-     ║
# ║  tional ingest in that era.  ERA5's value-add is its 0.25° grid  ║
# ║  in the satellite-era post-1980.                                 ║
# ║                                                                  ║
# ║  One CDS request per event bundles the full ±5-year same-month   ║
# ║  baseline window at the event's grid cell so the request fits    ║
# ║  in the CDS fast queue (~8000 hourly values, a few MB).          ║
# ║                                                                  ║
# ║  Requires a ~/.cdsapirc with a Copernicus API key.  The file     ║
# ║  is gitignored; the adapter never inlines the key.               ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Tier 3: daily Tmax via ECMWF ERA5 reanalysis (vendored from ThermoStrife v0.1.1)."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from thermofooty.config import CACHE_ERA5

# ─────────────────────────────────────────────────────────────────
#  Coverage + endpoint
# ─────────────────────────────────────────────────────────────────

# ERA5 itself reaches 1940 (back-extension), but the cascade hands the
# 1940-1980 window to 20CRv3 first: that dataset was built specifically
# to extend the historical record and has substantial observational
# ingest in this window, while ERA5's value-add is its 0.25° grid in
# the post-1980 era.  Restricting ERA5 to post-1980 also keeps CDS API
# traffic minimal (~one request per Arab-Spring-era event).
FIRST_YEAR: int = 1981
ERA5_DATASET: str = "reanalysis-era5-single-levels"
ERA5_VARIABLE: str = "2m_temperature"

ERA5_CACHE: Path = CACHE_ERA5


def covers(when: date) -> bool:
    """True if ``when`` falls inside the ERA5 coverage window."""
    return when.year >= FIRST_YEAR


# ─────────────────────────────────────────────────────────────────
#  Grid helpers  « ERA5 native grid is 0.25° »
# ─────────────────────────────────────────────────────────────────

_RES = 0.25


def _grid_cell(lat: float, lon: float) -> tuple[float, float]:
    """Nearest 0.25° cell centre for the cache key."""
    return round(lat / _RES) * _RES, round(lon / _RES) * _RES


def _cell_area(lat: float, lon: float) -> list[float]:
    """Tiny [N, W, S, E] bounding box for the single-point request."""
    cell_lat, cell_lon = _grid_cell(lat, lon)
    pad = _RES / 2
    return [cell_lat + pad, cell_lon - pad, cell_lat - pad, cell_lon + pad]


def _cache_path(lat: float, lon: float, year: int, month: int) -> Path:
    cell_lat, cell_lon = _grid_cell(lat, lon)
    return (
        ERA5_CACHE
        / f"{cell_lat:+07.2f}_{cell_lon:+08.2f}"
        / f"{year:04d}-{month:02d}.parquet"
    )


# ─────────────────────────────────────────────────────────────────
#  CDS request + daily-Tmax derivation
# ─────────────────────────────────────────────────────────────────


def _fetch_month_bundle(
    lat: float,
    lon: float,
    months_by_year: dict[int, list[int]],
) -> None:
    """Submit one CDS request that bundles all year-month pairs at the cell.

    Writes per-(year, month) parquet caches into ``ERA5_CACHE`` keyed by
    the rounded 0.25° cell containing ``(lat, lon)``.
    """
    import cdsapi
    import xarray as xr

    area = _cell_area(lat, lon)
    years = sorted({y for y in months_by_year})
    months = sorted({m for ms in months_by_year.values() for m in ms})
    # Days 1-31; CDS silently skips invalid ones (e.g. Feb 30).
    days = [f"{d:02d}" for d in range(1, 32)]
    hours = [f"{h:02d}:00" for h in range(24)]

    request = {
        "product_type": "reanalysis",
        "variable": ERA5_VARIABLE,
        "year": [str(y) for y in years],
        "month": [f"{m:02d}" for m in months],
        "day": days,
        "time": hours,
        "area": area,
        "format": "netcdf",
    }
    client = cdsapi.Client(quiet=True)
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tmp:
        target = Path(tmp.name)
    try:
        client.retrieve(ERA5_DATASET, request, str(target))
        ds = xr.open_dataset(target, decode_times=True)
    finally:
        if target.exists():
            target.unlink()

    # ERA5 variable name in the NetCDF is 't2m'; spatial dim is the small
    # area we requested.  Reduce hourly -> daily Tmax, spatial mean over the
    # tiny cell (typically a single grid point at 0.25 resolution).
    if "t2m" not in ds.variables:
        ds.close()
        return
    t2m = ds["t2m"]
    # Average over the (often single-point) spatial area, then resample.
    if "latitude" in t2m.dims:
        t2m = t2m.mean(dim=[d for d in ("latitude", "longitude") if d in t2m.dims])
    daily_max_k = t2m.resample(valid_time="1D").max() if "valid_time" in t2m.dims else \
        t2m.resample(time="1D").max()
    time_coord = "valid_time" if "valid_time" in daily_max_k.dims else "time"
    times = pd.to_datetime(daily_max_k[time_coord].values).date
    values_c = daily_max_k.values.astype("float64") - 273.15
    ds.close()

    series = pd.Series(values_c, index=pd.Index(times, name="date"))
    # Persist per year-month
    for (year, month), grp in series.groupby([
        pd.Index([d.year for d in series.index]),
        pd.Index([d.month for d in series.index]),
    ]):
        if year not in months_by_year or month not in months_by_year[year]:
            continue
        path = _cache_path(lat, lon, year, month)
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"tmax": grp.values}, index=grp.index).to_parquet(path)


def _load_month(lat: float, lon: float, year: int, month: int) -> pd.Series:
    """Return cached daily Tmax for one (year, month) at the cell, or empty."""
    path = _cache_path(lat, lon, year, month)
    if not path.exists():
        return pd.Series(dtype="float64")
    df = pd.read_parquet(path)
    return pd.Series(df["tmax"].values, index=df.index, dtype="float64")


def _ensure_cached(
    lat: float, lon: float, when: date,
    *, half_window_years: int = 5,
) -> None:
    """Submit one CDS request for any uncached (year, target_month) pairs."""
    target_month = when.month
    years = range(when.year - half_window_years, when.year + half_window_years + 1)
    missing: dict[int, list[int]] = {}
    for year in years:
        if year < FIRST_YEAR:
            continue
        if not _cache_path(lat, lon, year, target_month).exists():
            missing.setdefault(year, []).append(target_month)
    if not missing:
        return
    _fetch_month_bundle(lat, lon, missing)


# ─────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AnomalyFetch:
    """ERA5 event-day + baseline-window result."""

    tmax_event_c: float | None
    baseline: pd.DataFrame
    station_id: str
    provenance: str
    note: str = ""


def fetch_daily_tmax(lat: float, lon: float, when: date) -> float | None:
    """Return ERA5 daily Tmax (°C) for ``when`` at the cell nearest (lat, lon)."""
    if not covers(when):
        return None
    _ensure_cached(lat, lon, when)
    series = _load_month(lat, lon, when.year, when.month)
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
    """Same-month ±N-year ERA5 daily Tmax values, excluding event ± buffer."""
    _ensure_cached(lat, lon, when, half_window_years=half_window_years)
    target_month = when.month
    years = range(when.year - half_window_years, when.year + half_window_years + 1)
    buf = timedelta(days=event_buffer_days)
    event_lo, event_hi = when - buf, when + buf

    frames = []
    for year in years:
        if year < FIRST_YEAR:
            continue
        series = _load_month(lat, lon, year, target_month)
        series = series.dropna()
        if series.empty:
            continue
        if year == when.year:
            series = series[(series.index < event_lo) | (series.index > event_hi)]
        if not series.empty:
            frames.append(pd.DataFrame({"tmax": series.values}, index=series.index))
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
    """Resolve event-day and matching baseline window via ERA5."""
    if not covers(when):
        return AnomalyFetch(
            tmax_event_c=None,
            baseline=pd.DataFrame(columns=["tmax"]),
            station_id="",
            provenance="",
            note=f"{when.year} predates ERA5 ({FIRST_YEAR}+)",
        )
    try:
        tmax = fetch_daily_tmax(lat, lon, when)
    except Exception as exc:
        # cdsapi can fail for many reasons (network, quota, missing creds);
        # the cascade should fall through gracefully.
        return AnomalyFetch(
            tmax_event_c=None,
            baseline=pd.DataFrame(columns=["tmax"]),
            station_id="",
            provenance="",
            note=f"ERA5 request failed: {type(exc).__name__}: {exc}",
        )
    if tmax is None:
        cell_lat, cell_lon = _grid_cell(lat, lon)
        return AnomalyFetch(
            tmax_event_c=None,
            baseline=pd.DataFrame(columns=["tmax"]),
            station_id=f"ERA5_{cell_lat:+.2f}_{cell_lon:+.2f}",
            provenance="",
            note=f"ERA5 has no value for {when} at cell ({cell_lat:.2f}, {cell_lon:.2f})",
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
            station_id="ERA5",
            provenance="",
            note=f"ERA5 baseline only {len(baseline)} days (< {min_baseline_days})",
        )
    cell_lat, cell_lon = _grid_cell(lat, lon)
    return AnomalyFetch(
        tmax_event_c=tmax,
        baseline=baseline,
        station_id=f"ERA5_{cell_lat:+.2f}_{cell_lon:+.2f}",
        provenance="tier3_era5",
        note=(
            f"ERA5 cell ({cell_lat:.2f}, {cell_lon:.2f}); "
            f"baseline n={len(baseline)} same-month daily Tmax values"
        ),
    )


# Silence the cdsapi credentials sanity warning at import time if the key
# isn't configured — the adapter just won't return values until it is.
os.environ.setdefault("CDSAPI_RC", str(Path.home() / ".cdsapirc"))
