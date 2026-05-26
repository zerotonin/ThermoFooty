"""Weather-backfill tests — monkeypatched cascade, no network.

Builds an in-memory SQLite, seeds a handful of stadia + matches,
then exercises the backfill loop with a fake resolve_event_anomaly
that returns deterministic AnomalyFetch objects.  Covers:
  - tier resolution writes the right source_tier + station_id
  - anomaly = tmax_event_c - baseline.mean() arithmetic
  - altitude > 2000 m short-circuits to 'excluded_altitude'
  - cascade decline writes 'unverifiable' with NULL Tmax
  - select_pending_probes filters rows already in weather (idempotency)
  - coverage_by_tier + coverage_by_league_season aggregate correctly
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd
import pytest

from thermofooty.config import SCHEMA_SQL_PATH
from thermofooty.lookup import AnomalyFetch
from thermofooty.weather import backfill as backfill_mod
from thermofooty.weather.backfill import (
    BackfillProbe,
    backfill_iter,
    coverage_by_league_season,
    coverage_by_tier,
    resolve_probe,
    select_pending_probes,
)

# ─────────────────────────────────────────────────────────────────
#  Fixture: in-memory DB with one EPL league + 3 stadia + 4 matches
# ─────────────────────────────────────────────────────────────────


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))

    c.execute("INSERT INTO countries (iso_alpha2, name) VALUES ('EN', 'England')")
    country_id = c.execute("SELECT country_id FROM countries WHERE iso_alpha2='EN'").fetchone()[0]
    c.execute(
        "INSERT INTO leagues (country_id, name, tier, short_code, in_primary_panel) "
        "VALUES (?, 'Premier League', 1, 'EN_PREM', 1)",
        (country_id,),
    )
    league_id = c.execute("SELECT league_id FROM leagues WHERE short_code='EN_PREM'").fetchone()[0]

    # Three stadia: two sea-level, one at altitude > cap
    c.executemany(
        "INSERT INTO stadia (name, country_id, city, latitude, longitude, altitude_m, "
        "has_roof, qatar2022_cooled) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        [
            ("Anfield", country_id, "Liverpool", 53.4308, -2.9608, 57),
            ("Old Trafford", country_id, "Manchester", 53.4631, -2.2913, 38),
            ("Mt Everest XI Stadium", country_id, "Khumbu", 27.99, 86.92, 5364),
        ],
    )

    c.executemany(
        "INSERT INTO clubs (name, country_id) VALUES (?, ?)",
        [("Liverpool", country_id), ("Man United", country_id), ("Everest FC", country_id)],
    )
    liv = c.execute("SELECT club_id FROM clubs WHERE name='Liverpool'").fetchone()[0]
    mu = c.execute("SELECT club_id FROM clubs WHERE name='Man United'").fetchone()[0]
    eve = c.execute("SELECT club_id FROM clubs WHERE name='Everest FC'").fetchone()[0]
    def _sid(name: str) -> int:
        return c.execute("SELECT stadium_id FROM stadia WHERE name = ?", (name,)).fetchone()[0]
    stad_anf = _sid("Anfield")
    stad_ot = _sid("Old Trafford")
    stad_ev = _sid("Mt Everest XI Stadium")

    c.executemany(
        """
        INSERT INTO matches (
            league_id, season, match_date, home_club_id, away_club_id,
            stadium_id, data_tier, source_primary
        ) VALUES (?, '2022-23', ?, ?, ?, ?, 'B', 'football_data_uk')
        """,
        [
            (league_id, "2022-08-15", liv, mu, stad_anf),
            (league_id, "2023-03-05", mu, liv, stad_ot),
            (league_id, "2023-05-01", eve, liv, stad_ev),       # altitude excluded
            (league_id, "2023-08-19", liv, eve, stad_anf),      # second Anfield row
        ],
    )
    c.commit()
    yield c
    c.close()


# ─────────────────────────────────────────────────────────────────
#  Fake cascade  « returns deterministic AnomalyFetch per (lat, date) »
# ─────────────────────────────────────────────────────────────────


def _fake_cascade_factory():
    """Build a fake resolve_event_anomaly that returns canned fetches."""
    def fake(lat, lon, when, **kw):
        # Liverpool latitude → tier1 hit with anomaly +2.0
        if abs(lat - 53.4308) < 0.01:
            return AnomalyFetch(
                tmax_event_c=20.0,
                baseline=pd.DataFrame({"tmax": [18.0] * 30}),
                station_id="06214",
                provenance="tier1_ghcn",
                note="meteostat: fake",
            )
        # Manchester latitude → tier2 hit
        if abs(lat - 53.4631) < 0.01:
            return AnomalyFetch(
                tmax_event_c=12.5,
                baseline=pd.DataFrame({"tmax": [10.0, 11.0, 12.0, 13.0, 14.0] * 6}),
                station_id="HadCET",
                provenance="tier2_hadcet_max",
                note="HadCET max reading",
            )
        # Everything else → cascade declines
        return AnomalyFetch.empty(note="all tiers declined for this lat")
    return fake


# ─────────────────────────────────────────────────────────────────
#  select_pending_probes
# ─────────────────────────────────────────────────────────────────


def test_select_pending_probes_returns_all_matches_initially(conn):
    probes = select_pending_probes(conn)
    assert len(probes) == 4
    dates = [p.observation_date for p in probes]
    # Sorted ascending by match_date
    assert dates == sorted(dates)


def test_select_pending_probes_excludes_already_resolved(conn):
    # Pre-insert one weather row for the Anfield Aug-2022 match
    conn.execute(
        "INSERT INTO weather (stadium_id, observation_date, source_tier) "
        "VALUES ((SELECT stadium_id FROM stadia WHERE name='Anfield'), "
        "'2022-08-15', 'tier1_ghcn')"
    )
    probes = select_pending_probes(conn)
    assert len(probes) == 3
    assert all(
        not (p.observation_date == date(2022, 8, 15) and p.altitude_m == 57.0)
        for p in probes
    )


# ─────────────────────────────────────────────────────────────────
#  Single-probe resolution
# ─────────────────────────────────────────────────────────────────


def test_resolve_probe_writes_tier1_anomaly(monkeypatch):
    monkeypatch.setattr(backfill_mod, "resolve_event_anomaly", _fake_cascade_factory())
    probe = BackfillProbe(
        stadium_id=1, observation_date=date(2022, 8, 15),
        latitude=53.4308, longitude=-2.9608, altitude_m=57.0,
    )
    row = resolve_probe(probe)
    assert row.source_tier == "tier1_ghcn"
    assert row.tmax_obs_c == 20.0
    assert row.baseline_mean_c == 18.0
    assert row.baseline_n_days == 30
    assert row.tmax_anomaly_c == pytest.approx(2.0)


def test_resolve_probe_excludes_altitude_above_cap(monkeypatch):
    # Cascade must NOT be called for excluded-altitude rows
    called = []
    monkeypatch.setattr(
        backfill_mod, "resolve_event_anomaly",
        lambda *a, **k: called.append(True) or AnomalyFetch.empty(),
    )
    probe = BackfillProbe(
        stadium_id=3, observation_date=date(2023, 5, 1),
        latitude=27.99, longitude=86.92, altitude_m=5364.0,
    )
    row = resolve_probe(probe)
    assert row.source_tier == "excluded_altitude"
    assert row.tmax_obs_c is None
    assert not called, "cascade must be short-circuited for altitude-excluded probes"


def test_resolve_probe_writes_unverifiable_when_cascade_declines(monkeypatch):
    monkeypatch.setattr(backfill_mod, "resolve_event_anomaly", _fake_cascade_factory())
    probe = BackfillProbe(
        stadium_id=99, observation_date=date(2023, 7, 1),
        latitude=0.0, longitude=0.0, altitude_m=10.0,
    )
    row = resolve_probe(probe)
    assert row.source_tier == "unverifiable"
    assert row.tmax_obs_c is None
    assert row.tmax_anomaly_c is None


# ─────────────────────────────────────────────────────────────────
#  End-to-end loop
# ─────────────────────────────────────────────────────────────────


def test_backfill_iter_inserts_weather_rows(conn, monkeypatch):
    monkeypatch.setattr(backfill_mod, "resolve_event_anomaly", _fake_cascade_factory())
    probes = select_pending_probes(conn)
    rows = list(backfill_iter(conn, probes, commit_every=1))
    assert len(rows) == 4

    cur = conn.execute("SELECT count(*) FROM weather")
    assert int(cur.fetchone()[0]) == 4

    # The altitude-excluded probe must land as 'excluded_altitude'
    cur = conn.execute(
        "SELECT source_tier FROM weather WHERE observation_date = '2023-05-01'"
    )
    assert cur.fetchone()[0] == "excluded_altitude"


def test_backfill_iter_is_idempotent(conn, monkeypatch):
    monkeypatch.setattr(backfill_mod, "resolve_event_anomaly", _fake_cascade_factory())
    list(backfill_iter(conn, select_pending_probes(conn), commit_every=1))
    n_first = int(conn.execute("SELECT count(*) FROM weather").fetchone()[0])
    # Re-run from scratch: select_pending_probes should return zero, no new rows
    second_probes = select_pending_probes(conn)
    assert second_probes == []
    list(backfill_iter(conn, second_probes))
    n_second = int(conn.execute("SELECT count(*) FROM weather").fetchone()[0])
    assert n_first == n_second == 4


# ─────────────────────────────────────────────────────────────────
#  Coverage aggregation
# ─────────────────────────────────────────────────────────────────


def test_coverage_by_tier_breaks_down_inserted_rows(conn, monkeypatch):
    monkeypatch.setattr(backfill_mod, "resolve_event_anomaly", _fake_cascade_factory())
    list(backfill_iter(conn, select_pending_probes(conn), commit_every=1))
    tiers = {tc.source_tier: tc for tc in coverage_by_tier(conn)}
    # 2 Anfield matches → tier1; 1 Old Trafford → tier2; 1 Everest → excluded
    assert tiers["tier1_ghcn"].n_rows == 2
    assert tiers["tier2_hadcet_max"].n_rows == 1
    assert tiers["excluded_altitude"].n_rows == 1
    # fractions sum to 1.0
    assert abs(sum(tc.fraction for tc in tiers.values()) - 1.0) < 1e-9


def test_backfill_iter_parallel_matches_sequential(conn, monkeypatch):
    """workers > 1 must produce the same set of weather rows as
    workers = 1 — same source_tier breakdown, same total row count.
    The ThreadPoolExecutor path is an I/O speedup, not a behavioural
    change.
    """
    monkeypatch.setattr(backfill_mod, "resolve_event_anomaly", _fake_cascade_factory())
    # Sequential baseline
    list(backfill_iter(conn, select_pending_probes(conn), commit_every=1, workers=1))
    seq_rows = conn.execute(
        "SELECT source_tier, count(*) FROM weather GROUP BY source_tier ORDER BY source_tier"
    ).fetchall()

    # Wipe weather, re-run with workers=4
    conn.execute("DELETE FROM weather")
    conn.commit()
    list(backfill_iter(conn, select_pending_probes(conn), commit_every=1, workers=4))
    par_rows = conn.execute(
        "SELECT source_tier, count(*) FROM weather GROUP BY source_tier ORDER BY source_tier"
    ).fetchall()

    assert seq_rows == par_rows


def test_coverage_by_league_season_counts_matches_and_resolved(conn, monkeypatch):
    monkeypatch.setattr(backfill_mod, "resolve_event_anomaly", _fake_cascade_factory())
    list(backfill_iter(conn, select_pending_probes(conn), commit_every=1))
    cov = coverage_by_league_season(conn)
    # All four matches are EPL 2022-23 (per fixture, including the 2023-08-19 one
    # which we explicitly assigned season='2022-23' in the seed insert)
    assert len(cov) == 1
    assert cov[0].league_short_code == "EN_PREM"
    assert cov[0].n_matches == 4
    # 3 of 4 resolved (Anfield x2 + Old Trafford); Everest is excluded_altitude
    # which counts as NOT resolved by the SQL CASE in coverage_by_league_season.
    assert cov[0].n_resolved == 3
