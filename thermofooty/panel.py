# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — panel                                             ║
# ║  « materialise the analysis-ready (matches × weather × outcomes) ║
# ║    frame from SQLite into parquet for hypothesis fits »          ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Materialise the analysis_panel parquet from SQLite.

Phase 1 (scaffold): module is a stub.  Phase 4 (per dev plan)
materialises ``analysis_panel.parquet`` under
``thermofooty.config.DERIVED_DIR`` via a LEFT JOIN of the SQLite
``lineups`` × ``cards`` × ``weather`` × ``matches`` tables, with the
per-event ``card_received_in_match`` binary populated and the
pre-registered covariates attached.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def materialise_analysis_panel(*args: Any, **kwargs: Any) -> pd.DataFrame:
    """Build the analysis_panel from SQLite — Phase 4 stub."""
    raise NotImplementedError(
        "materialise_analysis_panel is a Phase-4 stub; landed after "
        "the football-data + fbref + weather ingestion passes complete."
    )
