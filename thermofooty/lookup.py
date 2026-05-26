# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — lookup                                            ║
# ║  « (lat, lon, date) → AnomalyFetch via the weather cascade »     ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Vendored from ThermoStrife v0.1.1 (lookup.py).                  ║
# ║  Source: https://doi.org/10.5281/zenodo.20371612                 ║
# ║  Sync date: 2026-05-26                                           ║
# ║  Behavioural change vs source: imports cascade tiers from        ║
# ║  thermofooty.weather (not thermostrife.sources); otherwise the   ║
# ║  cascade order, fallback semantics, and provenance strings are   ║
# ║  byte-identical so panels resolved here line up 1:1 with         ║
# ║  ThermoStrife's historical-uprisings panel.                      ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Cascade order:                                                  ║
# ║    1. GHCN-Daily / ECA&D / DWD via meteostat                     ║
# ║    2. Long-record observatories (HadCET; future: Paris/Berlin)   ║
# ║    3. ERA5 grid cell via cdsapi (1981+; 20CRv3 owns 1940-1980)   ║
# ║    4. 20CRv3 reanalysis grid cell (1806-1980)                    ║
# ║                                                                  ║
# ║  Each resolved row carries a temp_provenance flag so the         ║
# ║  inference layer can downweight low-tier resolutions in          ║
# ║  sensitivity analyses.                                           ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Stadium-day → (event_day Tmax, ±5-yr baseline) cascade resolver."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

# ─────────────────────────────────────────────────────────────────
#  Result dataclasses
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Resolution:
    """Single-day (lat, lon, date) lookup with no baseline window."""

    tmax_c: float | None
    provenance: str
    source_id: str
    note: str = ""


@dataclass(frozen=True)
class AnomalyFetch:
    """Event-day Tmax + matching same-source baseline window.

    Mirrors ``thermostrife.lookup.AnomalyFetch`` so the vendored
    cascade can return it unchanged.  By construction the baseline is
    built from the *same* underlying source / station that produced
    ``tmax_event_c``, so the anomaly
    ``tmax_event_c - baseline['tmax'].mean()`` is internally consistent
    (single-source on both sides — no apples-to-oranges mixing).
    """

    tmax_event_c: float | None
    baseline: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["tmax"]))
    station_id: str = ""
    provenance: str = "unverifiable"
    note: str = ""

    @classmethod
    def empty(cls, note: str = "") -> AnomalyFetch:
        return cls(
            tmax_event_c=None,
            baseline=pd.DataFrame(columns=["tmax"]),
            station_id="",
            provenance="unverifiable",
            note=note,
        )


# ─────────────────────────────────────────────────────────────────
#  Single-day cascade  « event-day-only Tmax, no baseline »
# ─────────────────────────────────────────────────────────────────


def resolve(
    lat: float,
    lon: float,
    when: date,
    *,
    station_hint: str | None = None,
    radius_km: float = 50.0,
) -> Resolution:
    """Resolve daily Tmax for ``when`` via the tier cascade."""
    # ── Tier 1: meteostat ────────────────────────────────────────
    try:
        from thermofooty.weather.meteostat_src import fetch_daily_tmax
    except ImportError:
        fetch_daily_tmax = None  # type: ignore[assignment]

    if fetch_daily_tmax is not None:
        tmax, source_id, note = fetch_daily_tmax(
            lat, lon, when, station_hint=station_hint, radius_km=radius_km
        )
        if tmax is not None:
            return Resolution(
                tmax_c=tmax, provenance="tier1_ghcn",
                source_id=source_id, note=note,
            )

    # ── Tier 2: HadCET (British Isles only for now) ──────────────
    try:
        from thermofooty.weather.hadcet_src import covers as hadcet_covers
        from thermofooty.weather.hadcet_src import fetch_daily_value
    except ImportError:
        hadcet_covers = None  # type: ignore[assignment]
        fetch_daily_value = None  # type: ignore[assignment]

    if hadcet_covers is not None and hadcet_covers(lat, lon):
        reading = fetch_daily_value(when)
        if reading is not None:
            return Resolution(
                tmax_c=reading.value_c,
                provenance=reading.provenance,
                source_id="HadCET",
                note=f"HadCET {reading.metric} reading",
            )

    # ── Tier 3: ERA5 reanalysis ──────────────────────────────────
    try:
        from thermofooty.weather.era5_src import covers as era5_covers
        from thermofooty.weather.era5_src import fetch_daily_tmax as era5_daily
    except ImportError:
        era5_covers = None  # type: ignore[assignment]
        era5_daily = None  # type: ignore[assignment]

    if era5_covers is not None and era5_covers(when):
        try:
            tmax = era5_daily(lat, lon, when)
        except Exception as exc:  # cdsapi failures
            tmax = None
            note = f"ERA5 request failed: {type(exc).__name__}: {exc}"
        else:
            note = "ERA5 0.25° grid cell"
        if tmax is not None:
            return Resolution(
                tmax_c=tmax, provenance="tier3_era5",
                source_id="ERA5", note=note,
            )

    # ── Tier 4: 20CRv3 reanalysis (1806-1980) ────────────────────
    try:
        from thermofooty.weather.twentycr_src import covers as cr_covers
        from thermofooty.weather.twentycr_src import fetch_daily_tmax as cr_daily
    except ImportError:
        cr_covers = None  # type: ignore[assignment]
        cr_daily = None  # type: ignore[assignment]

    if cr_covers is not None and cr_covers(when):
        try:
            tmax = cr_daily(lat, lon, when)
        except Exception as exc:
            tmax = None
            note = f"20CRv3 request failed: {type(exc).__name__}: {exc}"
        else:
            note = "20CRv3 1° grid cell"
        if tmax is not None:
            return Resolution(
                tmax_c=tmax, provenance="tier4_20crv3",
                source_id="20CRv3", note=note,
            )

    return Resolution(
        tmax_c=None,
        provenance="unverifiable",
        source_id="",
        note="all available tiers returned no value",
    )


# ─────────────────────────────────────────────────────────────────
#  Anomaly-fetch cascade  « event + matching baseline, one source »
# ─────────────────────────────────────────────────────────────────


#: Per-(tier, exception-type) deduplication for the resilience warnings
#: below.  A single missing-data condition (e.g. HadCET files not on disk)
#: would otherwise log 30,000 identical warnings during a backfill pass.
_TIER_ERROR_LOGGED: set[tuple[str, str]] = set()


def _try_tier(
    tier_name: str,
    fn,
    *args,
    **kwargs,
):
    """Call ``fn(*args, **kwargs)`` and return its AnomalyFetch, or None.

    Catches any exception, logs it once per (tier, exception-type) pair
    via ``warnings.warn``, and returns ``None`` so the cascade falls
    through to the next tier.  Programming bugs in our own code surface
    via the warning; transient upstream failures (network blips, missing
    files, rate limits) degrade gracefully.
    """
    import warnings
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        key = (tier_name, type(exc).__name__)
        if key not in _TIER_ERROR_LOGGED:
            _TIER_ERROR_LOGGED.add(key)
            warnings.warn(
                f"weather cascade tier {tier_name!r} raised "
                f"{type(exc).__name__}: {exc}.  This tier will be skipped "
                f"for the remainder of the process; subsequent identical "
                f"failures are silenced.",
                RuntimeWarning,
                stacklevel=2,
            )
        return None


def resolve_event_anomaly(
    lat: float,
    lon: float,
    when: date,
    *,
    half_window_years: int = 5,
    event_buffer_days: int = 7,
    min_baseline_days: int = 20,
    radius_km: float = 50.0,
) -> AnomalyFetch:
    """Cascade through tiers until one returns a consistent (event, baseline) pair.

    The returned ``AnomalyFetch`` carries event-day Tmax and a baseline
    DataFrame drawn from the *same* underlying series — never mixed
    across tiers — so the anomaly is internally consistent.

    Defaults reflect the OSF pre-registration:
    ``half_window_years=5`` (§ 3.4), ``event_buffer_days=7``,
    ``min_baseline_days=20``.

    Resilience: each tier call is wrapped in a guard that downgrades any
    raised exception to a one-shot warning + fall-through to the next
    tier.  A missing HadCET data file, a rate-limited meteostat
    request, or a CDS API outage no longer crashes a multi-hour backfill
    pass partway through.
    """
    # ── Tier 1: meteostat ────────────────────────────────────────
    try:
        from thermofooty.weather import meteostat_src
    except ImportError:
        meteostat_src = None  # type: ignore[assignment]

    if meteostat_src is not None:
        r = _try_tier(
            "tier1_ghcn", meteostat_src.resolve_for_anomaly,
            lat, lon, when,
            half_window_years=half_window_years,
            event_buffer_days=event_buffer_days,
            min_baseline_days=min_baseline_days,
            radius_km=radius_km,
        )
        if r is not None and r.tmax_event_c is not None:
            return AnomalyFetch(
                tmax_event_c=r.tmax_event_c,
                baseline=r.baseline,
                station_id=r.station_id,
                provenance="tier1_ghcn",
                note=r.note,
            )

    # ── Tier 2: HadCET (British Isles) ───────────────────────────
    try:
        from thermofooty.weather import hadcet_src
    except ImportError:
        hadcet_src = None  # type: ignore[assignment]

    if hadcet_src is not None and hadcet_src.covers(lat, lon):
        r = _try_tier(
            "tier2_hadcet", hadcet_src.resolve_for_anomaly,
            lat, lon, when,
            half_window_years=half_window_years,
            event_buffer_days=event_buffer_days,
            min_baseline_days=min_baseline_days,
        )
        if r is not None and r.tmax_event_c is not None:
            return AnomalyFetch(
                tmax_event_c=r.tmax_event_c,
                baseline=r.baseline,
                station_id=r.station_id,
                provenance=r.provenance,
                note=r.note,
            )

    # ── Tier 3: ERA5 (1981+) ─────────────────────────────────────
    try:
        from thermofooty.weather import era5_src
    except ImportError:
        era5_src = None  # type: ignore[assignment]

    if era5_src is not None and era5_src.covers(when):
        r = _try_tier(
            "tier3_era5", era5_src.resolve_for_anomaly,
            lat, lon, when,
            half_window_years=half_window_years,
            event_buffer_days=event_buffer_days,
            min_baseline_days=min_baseline_days,
        )
        if r is not None and r.tmax_event_c is not None:
            return AnomalyFetch(
                tmax_event_c=r.tmax_event_c,
                baseline=r.baseline,
                station_id=r.station_id,
                provenance=r.provenance,
                note=r.note,
            )

    # ── Tier 4: 20CRv3 (1806-1980) ───────────────────────────────
    try:
        from thermofooty.weather import twentycr_src
    except ImportError:
        twentycr_src = None  # type: ignore[assignment]

    if twentycr_src is not None and twentycr_src.covers(when):
        r = _try_tier(
            "tier4_20crv3", twentycr_src.resolve_for_anomaly,
            lat, lon, when,
            half_window_years=half_window_years,
            event_buffer_days=event_buffer_days,
            min_baseline_days=min_baseline_days,
        )
        if r is not None and r.tmax_event_c is not None:
            return AnomalyFetch(
                tmax_event_c=r.tmax_event_c,
                baseline=r.baseline,
                station_id=r.station_id,
                provenance=r.provenance,
                note=r.note,
            )

    return AnomalyFetch.empty(note="all available tiers returned no anomaly fetch")


# ─────────────────────────────────────────────────────────────────
#  Same-source single-day fetch  « for H3 surrounding-day queries »
# ─────────────────────────────────────────────────────────────────


def fetch_same_source_day(
    provenance: str,
    lat: float,
    lon: float,
    when: date,
    *,
    station_id: str | None = None,
    radius_km: float = 60.0,
) -> float | None:
    """Fetch Tmax at ``when`` via the same tier / station that resolved an event.

    Dispatch by ``provenance``: tier1 → meteostat (``station_id`` as a
    hint to pin the same station the cascade picked), tier2 → HadCET,
    tier3 → ERA5, tier4 → 20CRv3.  Returns ``None`` if the dispatched
    adapter has no value for that date.

    Used by H3 (within-event control) so surrounding-day Tmax values
    (t±1, t±7) come from the same series that produced the event-day
    reading — no apples-to-oranges mixing across the t-statistic.
    """
    if provenance == "tier1_ghcn":
        try:
            from thermofooty.weather.meteostat_src import fetch_daily_tmax
        except ImportError:
            return None
        tmax, _src, _note = fetch_daily_tmax(
            lat, lon, when, station_hint=station_id, radius_km=radius_km,
        )
        return tmax

    if provenance.startswith("tier2_hadcet"):
        try:
            from thermofooty.weather.hadcet_src import fetch_daily_value
        except ImportError:
            return None
        reading = fetch_daily_value(when)
        return reading.value_c if reading is not None else None

    if provenance == "tier3_era5":
        try:
            from thermofooty.weather.era5_src import fetch_daily_tmax as era5_fetch
        except ImportError:
            return None
        try:
            return era5_fetch(lat, lon, when)
        except Exception:
            return None

    if provenance == "tier4_20crv3":
        try:
            from thermofooty.weather.twentycr_src import fetch_daily_tmax as cr_fetch
        except ImportError:
            return None
        return cr_fetch(lat, lon, when)

    return None
