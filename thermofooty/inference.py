# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — inference                                         ║
# ║  « thin wrapper around reRandomStats for the OSF-locked battery » ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Pre-registered hypothesis-test orchestration on top of reRandomStats.

Every confirmatory and auxiliary estimator in ThermoFooty delegates
to ``rerandomstats`` v0.2.0+ — there is no parallel implementation of
the case-crossover / Wald / breakpoint math in this repo.  This
module is the thin orchestration layer that pulls the
``analysis_panel`` from SQLite (via :func:`thermofooty.panel.materialise_analysis_panel`),
maps it onto the events-list interchange convention that the
rerandomstats fitters expect, and produces the report dicts that
``thermofooty.viz`` consumes.

Phase 1 (scaffold): import-only stub.  Phase 5 (per dev plan) lands
``run_h1()`` — the single confirmatory primary fit — followed by the
three auxiliary batteries (league / dose-response / tournament) in
Phases 6a–6c.
"""

from __future__ import annotations

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


def run_h1() -> dict:
    """Confirmatory primary test (H1) — Phase 5 stub."""
    raise NotImplementedError(
        "run_h1 is a Phase-5 stub; lands after the analysis_panel "
        "materialiser (Phase 4) is wired."
    )
