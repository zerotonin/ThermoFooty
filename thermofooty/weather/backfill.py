# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — weather/backfill                                  ║
# ║  « run the cascade across every ingested match, fill `weather` » ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Reads (stadium_id, match_date) pairs from the matches table,    ║
# ║  calls thermofooty.lookup.resolve_event_anomaly() through the    ║
# ║  four-tier cascade, and writes one weather row per probe into    ║
# ║  the weather table.                                              ║
# ║                                                                  ║
# ║  Idempotent: any (stadium_id, match_date) already in weather is  ║
# ║  skipped, so re-runs after a partial crash pick up where the     ║
# ║  previous run stopped.  The UNIQUE (stadium_id, observation_date)║
# ║  constraint on weather is a belt-and-braces guard.               ║
# ║                                                                  ║
# ║  Altitude gate: per § 3.4 of the OSF pre-registration, stadia    ║
# ║  with altitude_m > 2000 are excluded from the analysis panel.    ║
# ║  Backfill writes them with source_tier='excluded_altitude' so    ║
# ║  the exclusion is auditable rather than silent.                  ║
# ║                                                                  ║
# ║  Cascade misses (all four tiers decline, or baseline < 20 days)  ║
# ║  land as source_tier='unverifiable' with tmax_obs_c=NULL — the   ║
# ║  inference layer can then filter or downweight as the analysis   ║
# ║  layer requires.                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Cascade-driven weather backfill across the ingested match panel."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pandas as pd

from thermofooty.constants import ALTITUDE_CAP_M
from thermofooty.lookup import AnomalyFetch, resolve_event_anomaly

# ─────────────────────────────────────────────────────────────────
#  Work-item + result types
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BackfillProbe:
    """One (stadium, date) pair pending a cascade lookup."""

    stadium_id: int
    observation_date: date
    latitude: float
    longitude: float
    altitude_m: float | None


@dataclass(frozen=True)
class WeatherRow:
    """One resolved weather row, ready for INSERT into the weather table."""

    stadium_id: int
    observation_date: date
    tmax_obs_c: float | None
    tmax_anomaly_c: float | None
    baseline_mean_c: float | None
    baseline_std_c: float | None
    baseline_n_days: int | None
    source_tier: str
    source_id: str
    note: str


@dataclass(frozen=True)
class BackfillStats:
    """End-of-run summary for the rich coverage table."""

    probed: int
    inserted: int
    skipped_already_present: int
    excluded_altitude: int
    unverifiable: int
    by_tier: dict[str, int]


# ─────────────────────────────────────────────────────────────────
#  Work-queue selection  « LEFT JOIN to filter already-resolved »
# ─────────────────────────────────────────────────────────────────


def select_pending_probes(conn: sqlite3.Connection) -> list[BackfillProbe]:
    """Return every (stadium, match_date) pair not yet in the weather table.

    Sorts by date ascending so the cascade caches warm in the same
    chronological order matches occurred — meteostat in particular
    benefits from same-month requests landing together.
    """
    cur = conn.execute(
        """
        SELECT DISTINCT m.stadium_id, m.match_date,
               s.latitude, s.longitude, s.altitude_m
        FROM matches m
        JOIN stadia s ON s.stadium_id = m.stadium_id
        LEFT JOIN weather w
               ON w.stadium_id = m.stadium_id
              AND w.observation_date = m.match_date
        WHERE w.weather_id IS NULL
        ORDER BY m.match_date ASC
        """
    )
    probes: list[BackfillProbe] = []
    for row in cur.fetchall():
        probes.append(
            BackfillProbe(
                stadium_id=int(row[0]),
                observation_date=date.fromisoformat(row[1]),
                latitude=float(row[2]),
                longitude=float(row[3]),
                altitude_m=float(row[4]) if row[4] is not None else None,
            )
        )
    return probes


# ─────────────────────────────────────────────────────────────────
#  Per-probe resolution
# ─────────────────────────────────────────────────────────────────


def _excluded_altitude_row(probe: BackfillProbe) -> WeatherRow:
    return WeatherRow(
        stadium_id=probe.stadium_id,
        observation_date=probe.observation_date,
        tmax_obs_c=None,
        tmax_anomaly_c=None,
        baseline_mean_c=None,
        baseline_std_c=None,
        baseline_n_days=None,
        source_tier="excluded_altitude",
        source_id="",
        note=(
            f"stadium altitude {probe.altitude_m:.0f} m > "
            f"{ALTITUDE_CAP_M} m exclusion (OSF § 3.4)"
        ),
    )


def _row_from_anomaly_fetch(
    probe: BackfillProbe, fetch: AnomalyFetch,
) -> WeatherRow:
    """Convert an AnomalyFetch into a weather-row dataclass for INSERT."""
    if fetch.tmax_event_c is None:
        return WeatherRow(
            stadium_id=probe.stadium_id,
            observation_date=probe.observation_date,
            tmax_obs_c=None,
            tmax_anomaly_c=None,
            baseline_mean_c=None,
            baseline_std_c=None,
            baseline_n_days=int(len(fetch.baseline)) if not fetch.baseline.empty else 0,
            source_tier=fetch.provenance,  # 'unverifiable' by construction
            source_id=fetch.station_id,
            note=fetch.note,
        )
    if "tmax" in fetch.baseline:
        baseline = fetch.baseline["tmax"].dropna()
    else:
        baseline = pd.Series(dtype="float64")
    mean = float(baseline.mean()) if not baseline.empty else None
    std = float(baseline.std()) if len(baseline) >= 2 else None
    anomaly = (
        float(fetch.tmax_event_c) - mean
        if mean is not None else None
    )
    return WeatherRow(
        stadium_id=probe.stadium_id,
        observation_date=probe.observation_date,
        tmax_obs_c=float(fetch.tmax_event_c),
        tmax_anomaly_c=anomaly,
        baseline_mean_c=mean,
        baseline_std_c=std,
        baseline_n_days=int(len(baseline)),
        source_tier=fetch.provenance,
        source_id=fetch.station_id,
        note=fetch.note,
    )


def resolve_probe(probe: BackfillProbe) -> WeatherRow:
    """Convert one probe into a weather row via cascade or altitude exclusion."""
    if probe.altitude_m is not None and probe.altitude_m > ALTITUDE_CAP_M:
        return _excluded_altitude_row(probe)
    fetch = resolve_event_anomaly(
        probe.latitude, probe.longitude, probe.observation_date,
    )
    return _row_from_anomaly_fetch(probe, fetch)


# ─────────────────────────────────────────────────────────────────
#  Insert  « INSERT OR IGNORE on UNIQUE (stadium_id, observation_date) »
# ─────────────────────────────────────────────────────────────────


def insert_weather_row(conn: sqlite3.Connection, row: WeatherRow) -> bool:
    """Insert one weather row.  Returns True if a row was written."""
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO weather (
            stadium_id, observation_date, tmax_obs_c, tmax_anomaly_c,
            baseline_mean_c, baseline_std_c, baseline_n_days,
            source_tier, source_id, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row.stadium_id, row.observation_date.isoformat(),
            row.tmax_obs_c, row.tmax_anomaly_c,
            row.baseline_mean_c, row.baseline_std_c, row.baseline_n_days,
            row.source_tier, row.source_id, row.note,
        ),
    )
    return conn.total_changes > before


# ─────────────────────────────────────────────────────────────────
#  Run loop  « yields per-probe so the CLI can attach a progress bar »
# ─────────────────────────────────────────────────────────────────


def backfill_iter(
    conn: sqlite3.Connection,
    probes: list[BackfillProbe],
    *,
    commit_every: int = 50,
) -> Iterator[WeatherRow]:
    """Yield each resolved WeatherRow as the loop progresses.

    Commits to SQLite every ``commit_every`` rows so a mid-run crash
    or Ctrl-C only loses the last batch, not the entire pass.  The
    caller is responsible for attaching a progress bar around the
    yielded values.
    """
    for i, probe in enumerate(probes, start=1):
        row = resolve_probe(probe)
        insert_weather_row(conn, row)
        if i % commit_every == 0:
            conn.commit()
        yield row
    conn.commit()


def backfill_all(
    conn: sqlite3.Connection,
    *,
    commit_every: int = 50,
    progress_callback=None,
) -> BackfillStats:
    """End-to-end: select pending probes, resolve each, insert into weather.

    ``progress_callback`` (if provided) is called with ``(i, n, row)``
    after each probe so the CLI can drive a progress bar without
    coupling the library to a specific UI.
    """
    probes = select_pending_probes(conn)
    n = len(probes)
    inserted = 0
    excluded = 0
    unverifiable = 0
    by_tier: dict[str, int] = {}
    for i, row in enumerate(backfill_iter(conn, probes, commit_every=commit_every), start=1):
        inserted += 1
        by_tier[row.source_tier] = by_tier.get(row.source_tier, 0) + 1
        if row.source_tier == "excluded_altitude":
            excluded += 1
        elif row.source_tier == "unverifiable":
            unverifiable += 1
        if progress_callback is not None:
            progress_callback(i, n, row)
    return BackfillStats(
        probed=n,
        inserted=inserted,
        skipped_already_present=0,   # select_pending_probes filters these out upstream
        excluded_altitude=excluded,
        unverifiable=unverifiable,
        by_tier=by_tier,
    )


# ─────────────────────────────────────────────────────────────────
#  Coverage report  « grouped by tier + by league × season »
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TierCoverage:
    source_tier: str
    n_rows: int
    fraction: float


def coverage_by_tier(conn: sqlite3.Connection) -> list[TierCoverage]:
    """Tier-level coverage breakdown across the weather table."""
    cur = conn.execute(
        """
        SELECT source_tier, count(*) AS n
        FROM weather
        GROUP BY source_tier
        ORDER BY n DESC
        """
    )
    rows = cur.fetchall()
    total = sum(int(r[1]) for r in rows) or 1
    return [
        TierCoverage(
            source_tier=str(r[0]),
            n_rows=int(r[1]),
            fraction=int(r[1]) / total,
        )
        for r in rows
    ]


@dataclass(frozen=True)
class LeagueSeasonCoverage:
    league_short_code: str
    season: str
    n_matches: int
    n_resolved: int
    n_unverifiable: int
    fraction_resolved: float


def coverage_by_league_season(conn: sqlite3.Connection) -> list[LeagueSeasonCoverage]:
    """Per-(league, season) coverage so we can flag low-quality cells."""
    cur = conn.execute(
        """
        SELECT
            l.short_code,
            m.season,
            count(DISTINCT m.match_id) AS n_matches,
            sum(CASE WHEN w.source_tier IS NOT NULL
                      AND w.source_tier NOT IN ('unverifiable', 'excluded_altitude')
                     THEN 1 ELSE 0 END) AS n_resolved,
            sum(CASE WHEN w.source_tier = 'unverifiable' THEN 1 ELSE 0 END) AS n_unverifiable
        FROM matches m
        JOIN leagues l ON l.league_id = m.league_id
        LEFT JOIN weather w
               ON w.stadium_id = m.stadium_id
              AND w.observation_date = m.match_date
        GROUP BY l.short_code, m.season
        ORDER BY l.short_code, m.season
        """
    )
    out: list[LeagueSeasonCoverage] = []
    for row in cur.fetchall():
        n = int(row[2])
        n_res = int(row[3] or 0)
        n_unv = int(row[4] or 0)
        out.append(
            LeagueSeasonCoverage(
                league_short_code=str(row[0]),
                season=str(row[1]),
                n_matches=n,
                n_resolved=n_res,
                n_unverifiable=n_unv,
                fraction_resolved=n_res / n if n > 0 else 0.0,
            )
        )
    return out


def record_provenance(
    conn: sqlite3.Connection, stats: BackfillStats,
) -> None:
    """Add one data_provenance row summarising the backfill pass."""
    accessed_at = datetime.now(UTC).isoformat(timespec="seconds")
    note = (
        f"probed={stats.probed} inserted={stats.inserted} "
        f"excluded_altitude={stats.excluded_altitude} "
        f"unverifiable={stats.unverifiable} "
        f"tiers={dict(sorted(stats.by_tier.items()))}"
    )
    conn.execute(
        """
        INSERT INTO data_provenance (
            source, accessed_at, n_rows_pulled, sha256_payload, notes
        ) VALUES ('cascade_backfill', ?, ?, NULL, ?)
        """,
        (accessed_at, stats.inserted, note),
    )
