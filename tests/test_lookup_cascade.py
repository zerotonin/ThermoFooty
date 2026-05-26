"""Cascade-resolver unit + integration tests.

Unit tests monkey-patch each tier so the cascade order, fall-through
semantics, and provenance plumbing are tested *without* hitting the
network.  A single ``@pytest.mark.network`` integration test exercises
the meteostat tier live so the cron-style cache warm-up in CI catches
upstream API breakage early.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from thermofooty import lookup


# ─────────────────────────────────────────────────────────────────
#  Fixture helpers  « pretend AnomalyFetch from a single tier »
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _StubFetch:
    """Mimics the AnomalyFetch returned by every tier's resolve_for_anomaly."""

    tmax_event_c: float | None
    baseline: pd.DataFrame
    station_id: str
    provenance: str = ""
    note: str = ""


def _fake_baseline(n: int = 30) -> pd.DataFrame:
    return pd.DataFrame({"tmax": [22.0 + (i % 5) for i in range(n)]})


# ─────────────────────────────────────────────────────────────────
#  Cascade order  « tier 1 wins outright »
# ─────────────────────────────────────────────────────────────────


def test_cascade_returns_tier1_when_meteostat_succeeds(monkeypatch):
    """When meteostat returns a usable fetch the cascade stops at tier 1."""
    from thermofooty.weather import meteostat_src

    def fake_resolve(lat, lon, when, **kwargs):
        return _StubFetch(
            tmax_event_c=30.5,
            baseline=_fake_baseline(),
            station_id="06240",  # de Bilt
            note="meteostat: 06240, n_baseline=30",
        )

    monkeypatch.setattr(meteostat_src, "resolve_for_anomaly", fake_resolve)
    result = lookup.resolve_event_anomaly(52.10, 5.18, date(2022, 7, 19))
    assert result.tmax_event_c == 30.5
    assert result.provenance == "tier1_ghcn"
    assert result.station_id == "06240"
    assert len(result.baseline) == 30


def test_cascade_falls_through_tier1_to_tier2(monkeypatch):
    """If meteostat declines but the event is inside the British-Isles
    bounding box, the cascade should land on HadCET (tier 2).
    """
    from thermofooty.weather import hadcet_src, meteostat_src

    monkeypatch.setattr(
        meteostat_src, "resolve_for_anomaly",
        lambda *a, **k: _StubFetch(None, pd.DataFrame(columns=["tmax"]), "", note="miss"),
    )
    monkeypatch.setattr(
        hadcet_src, "resolve_for_anomaly",
        lambda *a, **k: _StubFetch(
            tmax_event_c=24.1,
            baseline=_fake_baseline(40),
            station_id="HadCET",
            provenance="tier2_hadcet_max",
            note="HadCET max reading; baseline n=40",
        ),
    )
    # Manchester (Old Trafford), inside hadcet_src.covers() box
    result = lookup.resolve_event_anomaly(53.46, -2.29, date(2018, 6, 14))
    assert result.tmax_event_c == 24.1
    assert result.provenance == "tier2_hadcet_max"
    assert result.station_id == "HadCET"


def test_cascade_skips_hadcet_outside_british_isles(monkeypatch):
    """A Madrid event must not hit HadCET even if meteostat declines —
    the British-Isles bounding box check is the gate.
    """
    from thermofooty.weather import era5_src, hadcet_src, meteostat_src

    monkeypatch.setattr(
        meteostat_src, "resolve_for_anomaly",
        lambda *a, **k: _StubFetch(None, pd.DataFrame(columns=["tmax"]), ""),
    )

    hadcet_called = []
    monkeypatch.setattr(
        hadcet_src, "resolve_for_anomaly",
        lambda *a, **k: hadcet_called.append(True) or _StubFetch(
            None, pd.DataFrame(columns=["tmax"]), ""
        ),
    )
    monkeypatch.setattr(
        era5_src, "resolve_for_anomaly",
        lambda *a, **k: _StubFetch(
            tmax_event_c=38.7,
            baseline=_fake_baseline(50),
            station_id="ERA5_+40.50_-003.50",
            provenance="tier3_era5",
            note="ERA5 cell",
        ),
    )

    # Bernabéu — well outside the British-Isles box
    result = lookup.resolve_event_anomaly(40.45, -3.69, date(2022, 7, 5))
    assert result.tmax_event_c == 38.7
    assert result.provenance == "tier3_era5"
    assert not hadcet_called, "HadCET must be skipped outside its bbox"


def test_cascade_skips_era5_for_pre_1981(monkeypatch):
    """Events before ERA5's 1981 coverage floor should skip straight to
    20CRv3 (tier 4) once tier 1 and tier 2 decline.
    """
    from thermofooty.weather import era5_src, meteostat_src, twentycr_src

    monkeypatch.setattr(
        meteostat_src, "resolve_for_anomaly",
        lambda *a, **k: _StubFetch(None, pd.DataFrame(columns=["tmax"]), ""),
    )
    era5_called = []
    monkeypatch.setattr(
        era5_src, "resolve_for_anomaly",
        lambda *a, **k: era5_called.append(True) or _StubFetch(
            None, pd.DataFrame(columns=["tmax"]), ""
        ),
    )
    monkeypatch.setattr(
        twentycr_src, "resolve_for_anomaly",
        lambda *a, **k: _StubFetch(
            tmax_event_c=18.3,
            baseline=_fake_baseline(60),
            station_id="20CRv3_+52.00_+013.00",
            provenance="tier4_20crv3",
            note="20CRv3 1° cell",
        ),
    )

    # WM-Endspiel 1954 — pre-ERA5 coverage; 20CRv3 should take the call
    result = lookup.resolve_event_anomaly(47.38, 8.55, date(1954, 7, 4))
    assert result.tmax_event_c == 18.3
    assert result.provenance == "tier4_20crv3"
    assert not era5_called, "ERA5 must be skipped pre-1981 by covers()"


def test_cascade_returns_empty_when_all_tiers_decline(monkeypatch):
    """If every tier returns ``tmax_event_c=None`` the cascade emits the
    canonical ``AnomalyFetch.empty()`` sentinel so downstream code can
    flag the row with the ``unverifiable`` provenance.
    """
    from thermofooty.weather import era5_src, hadcet_src, meteostat_src, twentycr_src

    for mod in (meteostat_src, hadcet_src, era5_src, twentycr_src):
        monkeypatch.setattr(
            mod, "resolve_for_anomaly",
            lambda *a, **k: _StubFetch(None, pd.DataFrame(columns=["tmax"]), ""),
        )

    result = lookup.resolve_event_anomaly(40.45, -3.69, date(2022, 7, 5))
    assert result.tmax_event_c is None
    assert result.provenance == "unverifiable"
    assert result.baseline.empty
    assert list(result.baseline.columns) == ["tmax"]


# ─────────────────────────────────────────────────────────────────
#  fetch_same_source_day  « H3 helper, same-tier dispatch »
# ─────────────────────────────────────────────────────────────────


def test_fetch_same_source_day_dispatches_to_meteostat(monkeypatch):
    """``tier1_ghcn`` provenance must dispatch to meteostat and forward
    the station hint so the surrounding-day fetch hits the same station
    that resolved the event-day Tmax.
    """
    from thermofooty.weather import meteostat_src

    calls: dict[str, object] = {}

    def fake_daily(lat, lon, when, *, station_hint=None, radius_km=50.0):
        calls["station_hint"] = station_hint
        return 28.4, station_hint or "fallback", "meteostat fake"

    monkeypatch.setattr(meteostat_src, "fetch_daily_tmax", fake_daily)
    tmax = lookup.fetch_same_source_day(
        "tier1_ghcn", 52.10, 5.18, date(2022, 7, 20), station_id="06240",
    )
    assert tmax == 28.4
    assert calls["station_hint"] == "06240"


def test_fetch_same_source_day_unknown_provenance_returns_none():
    """Bogus provenance strings (legacy data, typos) must not crash —
    they degrade to ``None`` so the H3 t-test simply skips that row.
    """
    tmax = lookup.fetch_same_source_day(
        "tier99_made_up", 0.0, 0.0, date(2022, 7, 19),
    )
    assert tmax is None


def test_fetch_same_source_day_hadcet_variants(monkeypatch):
    """Both ``tier2_hadcet_max`` and ``tier2_hadcet_mean`` provenances
    must route to HadCET (the .startswith() dispatch is what handles
    pre-1878 events that fall back to the mean series).
    """
    from thermofooty.weather import hadcet_src

    monkeypatch.setattr(
        hadcet_src, "fetch_daily_value",
        lambda when: SimpleNamespace(
            value_c=16.2, metric="max", provenance="tier2_hadcet_max",
        ),
    )
    for prov in ("tier2_hadcet_max", "tier2_hadcet_mean"):
        assert lookup.fetch_same_source_day(
            prov, 53.46, -2.29, date(1870, 8, 1),
        ) == 16.2


# ─────────────────────────────────────────────────────────────────
#  Live meteostat integration  « opt-in via -m network »
# ─────────────────────────────────────────────────────────────────


@pytest.mark.network
def test_meteostat_live_resolves_de_bilt_event_day():
    """Smoke test: De Bilt (Netherlands) on a known summer date returns a
    sensible Tmax via the meteostat tier.  Runs only with -m network so
    routine CI stays offline.
    """
    pytest.importorskip("meteostat")
    result = lookup.resolve_event_anomaly(52.10, 5.18, date(2022, 7, 19))
    assert result.tmax_event_c is not None, result.note
    assert 25.0 <= result.tmax_event_c <= 45.0, (
        f"De Bilt 2022-07-19 Tmax = {result.tmax_event_c} °C — outside "
        f"the sanity envelope (record-breaking heatwave day)."
    )
    assert result.provenance == "tier1_ghcn"
    assert len(result.baseline) >= 20
