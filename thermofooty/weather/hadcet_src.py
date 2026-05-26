# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — weather/hadcet_src                                ║
# ║  « Tier-2 daily temperature via HadCET (1772+) »                 ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Vendored from ThermoStrife v0.1.1                               ║
# ║  Source: https://doi.org/10.5281/zenodo.20371612                 ║
# ║  Sync date: 2026-05-26                                           ║
# ║  Behavioural change vs source: HADCET_DIR re-rooted via          ║
# ║  thermofooty.config.RAW_HADCET so the three Met Office daily     ║
# ║  totals files load from the developer-configured data root;      ║
# ║  all algorithmic code byte-identical to the upstream release.    ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Hadley Centre Central England Temperature — three companion     ║
# ║  files mirrored under $THERMOFOOTY_DATA_ROOT/raw/observatories/  ║
# ║  hadcet/:                                                        ║
# ║    maxtemp_daily_totals.txt   daily Tmax,  1878+                 ║
# ║    meantemp_daily_totals.txt  daily mean,  1772+                 ║
# ║    mintemp_daily_totals.txt   daily Tmin,  1878+                 ║
# ║                                                                  ║
# ║  Used as a broad British-Isles proxy for events in England,      ║
# ║  Scotland, Wales and Ireland.  Pre-1878 events fall back to      ║
# ║  the mean series with a tier2_hadcet_mean provenance flag.       ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Tier 2: HadCET daily temperature (vendored from ThermoStrife v0.1.1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd

from thermofooty.config import RAW_HADCET

# ─────────────────────────────────────────────────────────────────
#  File paths
# ─────────────────────────────────────────────────────────────────

HADCET_DIR: Path = RAW_HADCET
MAX_FILE: Path = HADCET_DIR / "maxtemp_daily_totals.txt"
MEAN_FILE: Path = HADCET_DIR / "meantemp_daily_totals.txt"
MIN_FILE: Path = HADCET_DIR / "mintemp_daily_totals.txt"

# ─────────────────────────────────────────────────────────────────
#  British Isles bounding box  « rough Tier-2 dispatch region »
# ─────────────────────────────────────────────────────────────────

BRITISH_ISLES_BBOX = {
    "lat_min": 49.5,
    "lat_max": 60.5,
    "lon_min": -11.0,
    "lon_max": 2.0,
}


def covers(lat: float, lon: float) -> bool:
    """True if (lat, lon) falls within the HadCET British-Isles proxy box."""
    bb = BRITISH_ISLES_BBOX
    return (
        bb["lat_min"] <= lat <= bb["lat_max"]
        and bb["lon_min"] <= lon <= bb["lon_max"]
    )


# ─────────────────────────────────────────────────────────────────
#  Series loaders  « cached at process scope »
# ─────────────────────────────────────────────────────────────────


def _load_series(path: Path) -> pd.Series:
    """Load one Met Office daily-totals file into a Series indexed by date."""
    if not path.exists():
        raise FileNotFoundError(
            f"HadCET file missing: {path}. Re-run the Sprint 2 download "
            "(curl from https://www.metoffice.gov.uk/hadobs/hadcet/data/)."
        )
    # File layout: blank line, then header "Date Value", then daily rows.
    df = pd.read_csv(path, sep=r"\s+", skiprows=2, names=["date", "value"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    s = pd.Series(df["value"].values, index=df["date"], dtype="float64")
    s.index.name = "date"
    return s


@lru_cache(maxsize=1)
def load_max() -> pd.Series:
    return _load_series(MAX_FILE)


@lru_cache(maxsize=1)
def load_mean() -> pd.Series:
    return _load_series(MEAN_FILE)


# ─────────────────────────────────────────────────────────────────
#  Public adapter
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HadcetReading:
    value_c: float
    metric: str            # "max" or "mean"
    provenance: str        # "tier2_hadcet_max" or "tier2_hadcet_mean"


def fetch_daily_value(when: date) -> HadcetReading | None:
    """Return HadCET daily Tmax for ``when``, falling back to mean pre-1878."""
    max_s = load_max()
    if when in max_s.index:
        v = max_s.loc[when]
        if pd.notna(v):
            return HadcetReading(float(v), "max", "tier2_hadcet_max")
    mean_s = load_mean()
    if when in mean_s.index:
        v = mean_s.loc[when]
        if pd.notna(v):
            return HadcetReading(float(v), "mean", "tier2_hadcet_mean")
    return None


def fetch_baseline_window(
    when: date,
    *,
    half_window_years: int = 5,
    event_buffer_days: int = 7,
    metric: str = "max",
) -> pd.DataFrame:
    """Same-month ±N-year daily values, excluding event ± buffer.

    The ``metric`` argument must match the event-day reading's metric so
    the anomaly is computed against a like-for-like baseline; the
    resolver enforces this.
    """
    series = load_max() if metric == "max" else load_mean()
    target_month = when.month
    lo = when.replace(year=when.year - half_window_years)
    hi = when.replace(year=when.year + half_window_years)
    buf = timedelta(days=event_buffer_days)
    event_lo, event_hi = when - buf, when + buf

    # Build a date index of every same-month day in the window
    idx = series.index
    mask = (
        pd.Index([d.month for d in idx]) == target_month
    ) & (pd.Index(idx) >= lo) & (pd.Index(idx) <= hi)
    sub = series.loc[mask].dropna()
    sub = sub[(sub.index < event_lo) | (sub.index > event_hi)]
    return pd.DataFrame({"tmax": sub.values}, index=pd.Index(sub.index, name="date"))


@dataclass(frozen=True)
class AnomalyFetch:
    """HadCET event-day + baseline-window result."""

    tmax_event_c: float | None
    baseline: pd.DataFrame
    station_id: str
    provenance: str
    note: str = ""


def resolve_for_anomaly(
    lat: float,
    lon: float,
    when: date,
    *,
    half_window_years: int = 5,
    event_buffer_days: int = 7,
    min_baseline_days: int = 20,
) -> AnomalyFetch:
    """Resolve event-day reading and matching baseline window via HadCET."""
    if not covers(lat, lon):
        return AnomalyFetch(
            tmax_event_c=None,
            baseline=pd.DataFrame(columns=["tmax"]),
            station_id="",
            provenance="",
            note=f"({lat:.2f}, {lon:.2f}) outside HadCET British-Isles box",
        )
    reading = fetch_daily_value(when)
    if reading is None:
        return AnomalyFetch(
            tmax_event_c=None,
            baseline=pd.DataFrame(columns=["tmax"]),
            station_id="HadCET",
            provenance="",
            note=f"HadCET has no value for {when}",
        )
    baseline = fetch_baseline_window(
        when,
        half_window_years=half_window_years,
        event_buffer_days=event_buffer_days,
        metric=reading.metric,
    )
    if len(baseline) < min_baseline_days:
        return AnomalyFetch(
            tmax_event_c=None,
            baseline=baseline,
            station_id="HadCET",
            provenance="",
            note=(
                f"HadCET baseline only {len(baseline)} days "
                f"(< {min_baseline_days})"
            ),
        )
    return AnomalyFetch(
        tmax_event_c=reading.value_c,
        baseline=baseline,
        station_id="HadCET",
        provenance=reading.provenance,
        note=(
            f"HadCET {reading.metric} reading; baseline n={len(baseline)} "
            f"same-month {reading.metric} values"
        ),
    )
