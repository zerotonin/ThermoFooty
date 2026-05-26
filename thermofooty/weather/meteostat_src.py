# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — weather/meteostat_src                             ║
# ║  « Tier-1 daily Tmax via meteostat (GHCN / ECA&D / DWD) »        ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Vendored from ThermoStrife v0.1.1                               ║
# ║  Source: https://doi.org/10.5281/zenodo.20371612                 ║
# ║  Sync date: 2026-05-26                                           ║
# ║  Behavioural change vs source: cache path re-rooted via          ║
# ║  thermofooty.config.CACHE_METEOSTAT so the per-(station,         ║
# ║  year-month) parquet cache lands on the developer-configured     ║
# ║  data root; all algorithmic code byte-identical to upstream.     ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Resolves (lat, lon, date) into a single daily-max value or a    ║
# ║  monthly window of values, with parquet caching per (station,    ║
# ║  year-month) so dev cycles don't re-hit the upstream API.        ║
# ║                                                                  ║
# ║  meteostat 2.x bundles GHCN-Daily, ECA&D, and DWD into one       ║
# ║  Python wrapper and is the lightest-weight starting tier.        ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Tier 1: daily Tmax via meteostat (vendored from ThermoStrife v0.1.1)."""

from __future__ import annotations

import calendar
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from thermofooty.config import CACHE_METEOSTAT

# ─────────────────────────────────────────────────────────────────
#  Resolution dataclass  « mirrors thermofooty.lookup.AnomalyFetch »
# ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StationHit:
    """Nearest-station lookup result."""

    station_id: str
    name: str
    country: str
    distance_m: float


# ─────────────────────────────────────────────────────────────────
#  Cache layout: $THERMOFOOTY_DATA_ROOT/cache/meteostat/<station>/<YYYY-MM>.parquet
# ─────────────────────────────────────────────────────────────────

METEOSTAT_CACHE: Path = CACHE_METEOSTAT


def _cache_path(station_id: str, year: int, month: int) -> Path:
    safe = hashlib.sha1(station_id.encode()).hexdigest()[:12]
    return METEOSTAT_CACHE / safe / f"{station_id}_{year:04d}-{month:02d}.parquet"


def _load_or_fetch_month(station_id: str, year: int, month: int) -> pd.DataFrame:
    """Return a daily-frequency DataFrame for one calendar month at a station.

    Empty (zero-row) DataFrames are cached as negative results so we don't
    re-hit the API for known-missing months.
    """
    import meteostat as ms

    path = _cache_path(station_id, year, month)
    if path.exists():
        return pd.read_parquet(path)

    last_day = calendar.monthrange(year, month)[1]
    start = datetime(year, month, 1)
    end = datetime(year, month, last_day)
    ts = ms.daily(station_id, start=start, end=end)
    df = ts.fetch()
    df = pd.DataFrame(columns=["tmax"], dtype="float64") if df is None else df.copy()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


# ─────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────


def find_nearest_stations(
    lat: float,
    lon: float,
    *,
    radius_km: float = 50.0,
    limit: int = 8,
) -> list[StationHit]:
    """Nearest stations to (lat, lon) ordered by haversine distance.

    Args:
        lat:        Latitude in decimal degrees.
        lon:        Longitude in decimal degrees.
        radius_km:  Search radius in kilometres.
        limit:      Maximum number of stations to return.

    Returns:
        List of ``StationHit`` ordered nearest-first.  Empty if no station
        falls inside the radius.
    """
    import meteostat as ms

    df = ms.stations.nearby(
        ms.Point(lat, lon), radius=int(radius_km * 1000), limit=limit
    )
    if df.empty:
        return []
    return [
        StationHit(
            station_id=str(idx),
            name=str(row.get("name", "")),
            country=str(row.get("country", "")),
            distance_m=float(row.get("distance", float("nan"))),
        )
        for idx, row in df.iterrows()
    ]


def fetch_daily_tmax(
    lat: float,
    lon: float,
    when: date,
    *,
    station_hint: str | None = None,
    radius_km: float = 50.0,
    max_stations: int = 5,
) -> tuple[float | None, str, str]:
    """Resolve daily Tmax at (lat, lon) for ``when`` via meteostat.

    If ``station_hint`` is given it is queried directly.  Otherwise the
    nearest ``max_stations`` stations within ``radius_km`` are tried in
    order until one returns a non-null Tmax.

    Returns:
        ``(tmax_c, station_id, note)``.  ``tmax_c`` is ``None`` if no
        candidate station carried a value on that day.
    """
    candidates: list[str]
    if station_hint:
        candidates = [station_hint]
    else:
        hits = find_nearest_stations(lat, lon, radius_km=radius_km, limit=max_stations)
        candidates = [h.station_id for h in hits]
        if not candidates:
            return None, "", f"no station within {radius_km:.0f} km"

    for station_id in candidates:
        df = _load_or_fetch_month(station_id, when.year, when.month)
        if df.empty or "tmax" not in df.columns:
            continue
        # meteostat indexes daily DataFrames by a datetime-typed `time`.
        # Coerce both sides to a date for an exact match.
        try:
            df_dates = pd.to_datetime(df.index).date
        except Exception:  # pragma: no cover
            continue
        mask = df_dates == when
        if not mask.any():
            continue
        value = df.loc[mask, "tmax"].iloc[0]
        if pd.isna(value):
            continue
        return float(value), station_id, f"meteostat: {station_id}"
    return None, "", "no candidate station had data on this date"


def fetch_baseline_window(
    lat: float,
    lon: float,
    when: date,
    *,
    half_window_years: int = 5,
    event_buffer_days: int = 7,
    station_hint: str | None = None,
    radius_km: float = 50.0,
) -> tuple[pd.DataFrame, str]:
    """Same-station, same-calendar-month Tmax across ``year ± half_window_years``.

    Excludes the event window itself (`when ± event_buffer_days`).  The
    resolved station is the first nearby one that actually carries data
    in the target month-year range; the empty calendar months of stale
    candidates are cached as empty parquets so the search converges fast
    on subsequent runs.
    """
    candidates: list[str]
    if station_hint:
        candidates = [station_hint]
    else:
        hits = find_nearest_stations(lat, lon, radius_km=radius_km, limit=8)
        candidates = [h.station_id for h in hits]
        if not candidates:
            return pd.DataFrame(columns=["tmax"]), ""

    target_month = when.month
    years = range(when.year - half_window_years, when.year + half_window_years + 1)
    buffer = timedelta(days=event_buffer_days)
    event_lo, event_hi = when - buffer, when + buffer

    for station_id in candidates:
        frames = []
        for year in years:
            df = _load_or_fetch_month(station_id, year, target_month)
            if df.empty or "tmax" not in df.columns:
                continue
            df = df[df["tmax"].notna()]
            if df.empty:
                continue
            df_dates = pd.to_datetime(df.index).date
            # Drop the event window from the same year only
            if year == when.year:
                df = df.loc[(df_dates < event_lo) | (df_dates > event_hi)]
            if not df.empty:
                frames.append(df[["tmax"]])
        if frames:
            full = pd.concat(frames)
            return full, station_id

    return pd.DataFrame(columns=["tmax"]), ""


# ─────────────────────────────────────────────────────────────────
#  Unified resolver  « single station for event AND baseline »
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AnomalyFetch:
    """Combined event-day + baseline-window result for one event.

    By construction the same ``station_id`` produced both ``tmax_event_c``
    and ``baseline``, so the anomaly ``tmax_event_c - baseline['tmax'].mean()``
    is internally consistent (no apples-to-oranges station mixing).
    """

    tmax_event_c: float | None
    baseline: pd.DataFrame
    station_id: str
    note: str = ""


def resolve_for_anomaly(
    lat: float,
    lon: float,
    when: date,
    *,
    half_window_years: int = 5,
    event_buffer_days: int = 7,
    min_baseline_days: int = 20,
    station_hint: str | None = None,
    radius_km: float = 50.0,
    max_stations: int = 8,
) -> AnomalyFetch:
    """Pick one station that satisfies *both* event-day and baseline coverage.

    Iterates nearest-first through candidate stations and accepts the first
    that returns a non-null event-day Tmax AND at least
    ``min_baseline_days`` daily Tmax values across the ±N-year same-month
    window.  This is the resolver the case-crossover analysis should call —
    it guarantees the anomaly is computed from a single station's record.

    Returns:
        ``AnomalyFetch`` with ``tmax_event_c is None`` if no candidate
        station satisfies both criteria.
    """
    candidates: list[str]
    if station_hint:
        candidates = [station_hint]
    else:
        hits = find_nearest_stations(lat, lon, radius_km=radius_km, limit=max_stations)
        candidates = [h.station_id for h in hits]
        if not candidates:
            return AnomalyFetch(
                tmax_event_c=None,
                baseline=pd.DataFrame(columns=["tmax"]),
                station_id="",
                note=f"no station within {radius_km:.0f} km",
            )

    target_month = when.month
    years = range(when.year - half_window_years, when.year + half_window_years + 1)
    buffer = timedelta(days=event_buffer_days)
    event_lo, event_hi = when - buffer, when + buffer

    last_note = "no candidate satisfied both event-day and baseline coverage"
    for station_id in candidates:
        # Event-day Tmax for this candidate
        ev_df = _load_or_fetch_month(station_id, when.year, target_month)
        if ev_df.empty or "tmax" not in ev_df.columns:
            continue
        ev_dates = pd.to_datetime(ev_df.index).date
        mask = ev_dates == when
        if not mask.any():
            continue
        tmax_event = ev_df.loc[mask, "tmax"].iloc[0]
        if pd.isna(tmax_event):
            continue

        # Baseline window for the same station
        frames = []
        for year in years:
            df = _load_or_fetch_month(station_id, year, target_month)
            if df.empty or "tmax" not in df.columns:
                continue
            df = df[df["tmax"].notna()]
            if df.empty:
                continue
            df_dates = pd.to_datetime(df.index).date
            if year == when.year:
                df = df.loc[(df_dates < event_lo) | (df_dates > event_hi)]
            if not df.empty:
                frames.append(df[["tmax"]])
        if not frames:
            last_note = f"station {station_id} had event day but no baseline"
            continue
        baseline = pd.concat(frames)
        if len(baseline) < min_baseline_days:
            last_note = (
                f"station {station_id} baseline only {len(baseline)} days "
                f"(< {min_baseline_days})"
            )
            continue
        return AnomalyFetch(
            tmax_event_c=float(tmax_event),
            baseline=baseline,
            station_id=station_id,
            note=f"meteostat: {station_id}, n_baseline={len(baseline)}",
        )

    return AnomalyFetch(
        tmax_event_c=None,
        baseline=pd.DataFrame(columns=["tmax"]),
        station_id="",
        note=last_note,
    )
