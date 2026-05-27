"""H1 case-crossover inference tests (offline, synthetic panel).

build_h1_events:
  - emits one event per (case match-side) within (club, year-month)
  - skips singleton strata (no controls)
  - skips strata where every match has outcome 0 (no cases)
  - exposure goes onto tmax_event_c; controls populate baseline tmax

run_h1:
  - threads the events list through rerandomstats and reports the
    OR + CI + p-values on a constructed panel where the true effect
    is known.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from thermofooty.inference import build_h1_events, run_h1
from thermofooty.panel import PANEL_COLUMNS


def _empty_panel_row(**overrides) -> dict:
    """Minimal panel row populated with sensible defaults; override per row."""
    row = {col: None for col in PANEL_COLUMNS}
    row.update({
        "league_short_code": "EN_PREM", "season": "2022-23",
        "is_home": 1, "side": "home",
        "stadium_id": 1, "stadium_name": "Anfield",
        "latitude": 53.43, "longitude": -2.96, "altitude_m": 57.0,
        "source_tier": "tier1_ghcn", "source_id": "06214",
        "baseline_mean_c": 18.0, "baseline_std_c": 2.0, "baseline_n_days": 30,
        "n_cards_total": 0, "n_reds_total": pd.NA,
        "side_received_card": 0, "side_received_red": pd.NA,
    })
    row.update(overrides)
    return row


# ─────────────────────────────────────────────────────────────────
#  build_h1_events  « per-stratum case-control bundling »
# ─────────────────────────────────────────────────────────────────


def test_build_events_emits_one_event_per_case_match_side():
    """Liverpool plays 3 matches in Aug-2022, one with a card.  Expect
    one event whose case is the carded match and whose baseline has 2
    control rows.
    """
    panel = pd.DataFrame([
        _empty_panel_row(
            match_id=1, match_date=date(2022, 8, 7), club_id=1,
            tmax_anomaly_c=-0.5, n_cards_total=0, side_received_card=0,
        ),
        _empty_panel_row(
            match_id=2, match_date=date(2022, 8, 15), club_id=1,
            tmax_anomaly_c=+3.0, n_cards_total=2, side_received_card=1,
        ),
        _empty_panel_row(
            match_id=3, match_date=date(2022, 8, 28), club_id=1,
            tmax_anomaly_c=+0.8, n_cards_total=0, side_received_card=0,
        ),
    ])
    events = build_h1_events(panel)
    assert len(events) == 1
    ev = events[0]
    assert ev["when"] == date(2022, 8, 15)
    assert ev["tmax_event_c"] == 3.0
    assert len(ev["baseline"]) == 2
    assert set(ev["baseline"]["tmax"].tolist()) == {-0.5, 0.8}


def test_build_events_skips_singleton_strata():
    """A team's only match in a month can't form a case-control bundle."""
    panel = pd.DataFrame([
        _empty_panel_row(
            match_id=10, match_date=date(2022, 9, 4), club_id=2,
            tmax_anomaly_c=+1.0, n_cards_total=3, side_received_card=1,
        ),
    ])
    assert build_h1_events(panel) == []


def test_build_events_skips_strata_with_no_case():
    """A team-month where every match was card-free emits no event."""
    panel = pd.DataFrame([
        _empty_panel_row(
            match_id=20, match_date=date(2022, 10, 2), club_id=3,
            tmax_anomaly_c=+0.4, n_cards_total=0, side_received_card=0,
        ),
        _empty_panel_row(
            match_id=21, match_date=date(2022, 10, 16), club_id=3,
            tmax_anomaly_c=+1.7, n_cards_total=0, side_received_card=0,
        ),
    ])
    assert build_h1_events(panel) == []


def test_build_events_emits_two_events_when_both_matches_are_cases():
    """If both matches in a stratum are cases, each gets its own event
    with the OTHER match as the sole control row.
    """
    panel = pd.DataFrame([
        _empty_panel_row(
            match_id=30, match_date=date(2023, 4, 1), club_id=4,
            tmax_anomaly_c=+2.0, n_cards_total=1, side_received_card=1,
        ),
        _empty_panel_row(
            match_id=31, match_date=date(2023, 4, 22), club_id=4,
            tmax_anomaly_c=-1.0, n_cards_total=2, side_received_card=1,
        ),
    ])
    events = build_h1_events(panel)
    assert len(events) == 2
    for ev in events:
        assert len(ev["baseline"]) == 1


def test_build_events_drops_rows_with_nan_exposure():
    """A row with tmax_anomaly_c = NaN must not appear as case OR control."""
    panel = pd.DataFrame([
        _empty_panel_row(
            match_id=40, match_date=date(2023, 5, 1), club_id=5,
            tmax_anomaly_c=float("nan"), n_cards_total=2, side_received_card=1,
        ),
        _empty_panel_row(
            match_id=41, match_date=date(2023, 5, 10), club_id=5,
            tmax_anomaly_c=+0.5, n_cards_total=0, side_received_card=0,
        ),
        _empty_panel_row(
            match_id=42, match_date=date(2023, 5, 20), club_id=5,
            tmax_anomaly_c=+2.0, n_cards_total=1, side_received_card=1,
        ),
    ])
    events = build_h1_events(panel)
    # The NaN case row drops out; only match 42's case survives, with
    # match 41 as its sole control.
    assert len(events) == 1
    assert events[0]["when"] == date(2023, 5, 20)


# ─────────────────────────────────────────────────────────────────
#  run_h1  « end-to-end with a known signal »
# ─────────────────────────────────────────────────────────────────


def _synthetic_panel_with_signal(
    n_clubs: int = 40,
    matches_per_month: int = 4,
    n_months: int = 12,
    beta: float = 0.20,
    seed: int = 42,
) -> pd.DataFrame:
    """Construct a panel where the true beta is ``beta``.

    Each (club, year-month) gets ``matches_per_month`` matches with
    random anomalies; outcome ~ Bernoulli(sigmoid(beta * anomaly)).
    With ~1900 events the conditional logit should recover the true
    beta to within ~0.04 at 5% nominal alpha.
    """
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    for club in range(n_clubs):
        for month in range(1, n_months + 1):
            for k in range(matches_per_month):
                anomaly = float(rng.normal(0.0, 3.0))
                p = 1.0 / (1.0 + np.exp(-(beta * anomaly)))
                received = int(rng.uniform() < p)
                rows.append(_empty_panel_row(
                    match_id=club * 10_000 + month * 100 + k,
                    match_date=date(2022, month, 1 + k * 5),
                    club_id=club + 1,
                    tmax_anomaly_c=anomaly,
                    n_cards_total=received,
                    side_received_card=received,
                ))
    return pd.DataFrame(rows)


def test_run_h1_recovers_direction_and_significance_of_known_signal():
    """End-to-end: a synthetic panel with true β = +0.20 / °C must
    produce an OR > 1, a CI that excludes 1.0, and a one-sided p in
    the OSF direction far below 0.05.

    We don't assert exact-β recovery: the inclusive-period case-
    crossover (Lee et al. 2023) treats other cases within a stratum
    as is_case=0 controls, which attenuates the magnitude of β in a
    design-inherent way.  Sign + significance are what we need from
    this smoke test — exact-magnitude recovery is a job for the
    rerandomstats own test suite, not for ThermoFooty's wiring test.
    """
    panel = _synthetic_panel_with_signal(beta=0.20)
    result = run_h1(panel, covariates=[])  # disable daylight covariate for the synthetic test
    assert not result.get("skipped"), result.get("reason")
    assert result["beta"] > 0, f"β should be positive, got {result['beta']:.4f}"
    assert result["or_per_degree"] > 1.0
    ci_lo, ci_hi = result["or_ci95"]
    assert ci_lo > 1.0, (
        f"CI lower bound should exceed 1.0 for a significant "
        f"positive effect, got {ci_lo:.3f}"
    )
    assert ci_hi > ci_lo
    assert result["pvalue_one_sided_pos"] < 0.001


def test_run_h1_skips_when_panel_yields_no_events():
    """A panel where every team-month is a singleton emits zero events;
    run_h1 returns a skipped marker instead of fitting nothing.
    """
    panel = pd.DataFrame([
        _empty_panel_row(
            match_id=99, match_date=date(2023, 7, 4), club_id=99,
            tmax_anomaly_c=+1.0, n_cards_total=1, side_received_card=1,
        ),
    ])
    result = run_h1(panel)
    assert result.get("skipped") is True
    assert result["n_events"] == 0


def test_run_h1_unknown_outcome_column_raises():
    panel = pd.DataFrame([_empty_panel_row()])
    with pytest.raises(KeyError, match="not in panel"):
        run_h1(panel, outcome_col="no_such_column")
