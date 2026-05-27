# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — inference                                         ║
# ║  « thin wrapper around reRandomStats for the OSF-locked battery » ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Pre-registered hypothesis-test orchestration on top of reRandomStats.

Every confirmatory and auxiliary estimator in ThermoFooty delegates
to ``rerandomstats`` v0.2.0+ — there is no parallel implementation of
the case-crossover / Wald / breakpoint math in this repo.  This
module is the thin orchestration layer that pulls the
``analysis_panel`` from :func:`thermofooty.panel.materialise_analysis_panel`,
maps it onto the events-list interchange convention that the
rerandomstats fitters expect, and produces the report dicts that
``thermofooty.viz`` consumes.

Phase 5a (this revision): ``run_h1`` runs the case-crossover
conditional logit on the side-received-card proxy outcome (the only
outcome available from football-data.co.uk's match-level aggregates).
The OSF-locked confirmatory test uses red-cards-for-violent-conduct;
that swap lands in Phase 5b alongside the fbref ingestion that
unlocks per-card reason codes.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

# Eager imports to fail loudly at module load if rerandomstats v0.2.0
# is not available — the OSF pre-registration explicitly delegates
# to this toolkit, so a missing dependency must surface immediately
# rather than several minutes into an analysis run.
import rerandomstats as rrs  # noqa: F401
from rerandomstats import (  # noqa: F401
    benjamini_hochberg,
    broken_stick_fit,
    build_case_crossover_frame,
    case_crossover_conditional_logit,
    correct_pvalues,
    correct_pvalues_array,
    davies_test,
    hill_fit,
    hsiang_sigma_rescaled,
    likelihood_ratio_test,
    per_subject_segmented,
    pscore_test,
    stratified_permutation,
    wald_two_sample_beta,
)

# ─────────────────────────────────────────────────────────────────
#  Stratum key  « (club_id, year-month) — the time-stratified subject »
# ─────────────────────────────────────────────────────────────────


def _year_month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


# ─────────────────────────────────────────────────────────────────
#  Event builder  « one event per (case match-side) with month-mates »
# ─────────────────────────────────────────────────────────────────


def build_h1_events(
    panel: pd.DataFrame,
    *,
    outcome_col: str = "side_received_card",
) -> list[dict]:
    """Build the rerandomstats events list for the H1 case-crossover.

    Strata: ``(club_id, year-month)`` — each team's matches in a
    calendar month form one stratum.  For every match within a
    stratum where the side's ``outcome_col`` was 1, emit one event
    whose case is that match and whose controls are the team's OTHER
    matches in the same year-month.  Strata with no qualifying case
    (every match had outcome 0) emit no events; strata with no
    eligible controls (a single match that month) emit no events
    either — the conditional likelihood needs both.

    ``outcome_col`` defaults to the side-received-card proxy that
    football-data.co.uk supports; swap to ``"side_received_red"``
    once Phase 3 fbref ingestion populates the reds column.
    """
    if outcome_col not in panel.columns:
        raise KeyError(
            f"outcome column {outcome_col!r} not in panel.  "
            f"Available: {sorted(panel.columns)}"
        )
    # Drop rows missing exposure or outcome
    work = panel.dropna(subset=["tmax_anomaly_c", outcome_col]).copy()
    work["match_date"] = pd.to_datetime(work["match_date"]).dt.date
    work["year_month"] = work["match_date"].map(_year_month_key)

    events: list[dict] = []
    for (club_id, ym), grp in work.groupby(["club_id", "year_month"], sort=False):
        if len(grp) < 2:
            # Need at least one case + one control in the stratum
            continue
        case_rows = grp[grp[outcome_col] == 1]
        if case_rows.empty:
            continue
        for _, case in case_rows.iterrows():
            controls = grp[grp["match_date"] != case["match_date"]]
            if controls.empty:
                continue
            baseline = pd.DataFrame(
                {"tmax": controls["tmax_anomaly_c"].to_numpy()},
                index=pd.Index(
                    list(controls["match_date"].to_numpy()), name="date",
                ),
            )
            events.append(
                {
                    "event_id": f"{club_id}_{ym}_{case['match_id']}_{case['side']}",
                    "lat": float(case["latitude"]),
                    "lon": float(case["longitude"]),
                    "when": case["match_date"],
                    "tmax_event_c": float(case["tmax_anomaly_c"]),
                    "baseline": baseline,
                }
            )
    return events


# ─────────────────────────────────────────────────────────────────
#  Top-level H1 driver
# ─────────────────────────────────────────────────────────────────


def run_h1(
    panel: pd.DataFrame,
    *,
    outcome_col: str = "side_received_card",
    covariates: list[str] | None = None,
) -> dict:
    """Run the H1 case-crossover conditional logit on the analysis panel.

    Args:
        panel:        Output of
                      :func:`thermofooty.panel.materialise_analysis_panel`.
        outcome_col:  Binary outcome variable.  Defaults to the
                      side-received-card proxy.  Swap to
                      ``"side_received_red"`` once Phase 3 fbref data lands.
        covariates:   Extra columns for the conditional logit; default
                      ``["daylight_h"]`` (computed by
                      :func:`rerandomstats.build_case_crossover_frame`).

    Returns:
        Dict carrying the headline H1 estimates plus event-build
        metadata.  Keys:
          - ``proxy_outcome`` (str) — name of the outcome column used
          - ``n_events`` (int)      — events built from the panel
          - ``n_strata_in_fit`` (int)
          - ``or_per_degree`` (float)  — odds ratio per +1 °C anomaly
          - ``or_ci95`` (tuple[float, float])
          - ``beta`` (float), ``se`` (float)
          - ``pvalue_two_sided`` (float)
          - ``pvalue_one_sided_pos`` (float) — OSF-locked test
          - ``raw`` (dict) — full rerandomstats fit dict
    """
    events = build_h1_events(panel, outcome_col=outcome_col)
    if not events:
        return {
            "proxy_outcome": outcome_col,
            "n_events": 0,
            "skipped": True,
            "reason": "no events built from panel (no team-month with both a case and a control)",
        }
    frame = build_case_crossover_frame(events)
    fit = case_crossover_conditional_logit(frame, covariates=covariates)
    if fit.get("skipped"):
        return {
            "proxy_outcome": outcome_col,
            "n_events": len(events),
            "skipped": True,
            "reason": fit.get("reason", "rerandomstats skipped the fit"),
            "raw": fit,
        }
    return {
        "proxy_outcome": outcome_col,
        "n_events": len(events),
        "n_events_in_fit": int(fit["n_events"]),
        "n_rows_in_fit": int(fit["n_rows"]),
        "or_per_degree": float(fit["or_per_C"]),
        "or_ci95": (float(fit["or_ci95_low"]), float(fit["or_ci95_high"])),
        "beta": float(fit["beta_per_C"]),
        "se": float(fit["se_per_C"]),
        "pvalue_two_sided": float(fit["pvalue_two_sided"]),
        # rerandomstats returns the upper-tail one-sided p (β > 0); the
        # OSF-locked direction for H1 is "heat raises aggression", so the
        # upper-tail p is the right one to report.
        "pvalue_one_sided_pos": float(fit["pvalue_one_sided"]),
        "covariate_betas": dict(fit["covariate_betas"]),
        "raw": fit,
    }
