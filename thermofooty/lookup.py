# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — lookup                                            ║
# ║  « (stadium_id, date) → AnomalyFetch via the weather cascade »   ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Stadium-day → (event_day Tmax, ±5-yr baseline) resolver.

Phase 1 (scaffold): module is a typed stub.  Phase 2 wires this to
the vendored ``thermofooty.weather`` cascade so each (stadium_id,
match_date) query returns an ``AnomalyFetch`` carrying the event-day
Tmax, the same-source baseline window, the provenance tier, and the
station / grid-cell identifier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class AnomalyFetch:
    """Stadium-day anomaly result, one per (stadium, match_date) call.

    Mirrors the ``thermostrife.lookup.AnomalyFetch`` shape so the
    vendored cascade can return it unchanged.  Carries event-day Tmax,
    a same-source baseline window, the provenance tier
    (``tier1_ghcn`` / ``tier2_hadcet_*`` / ``tier3_era5`` /
    ``tier4_20crv3`` / ``unverifiable``), and the station / grid-cell
    identifier.  By construction the anomaly
    ``tmax_event_c - baseline["tmax"].mean()`` is internally consistent
    (single-source on both sides).
    """

    tmax_event_c: float | None
    baseline: pd.DataFrame = field(default_factory=pd.DataFrame)
    station_id: str = ""
    provenance: str = "unverifiable"
    note: str = ""


def resolve_event_anomaly(*args: Any, **kwargs: Any) -> AnomalyFetch:
    """Tiered cascade resolver — Phase 2 stub.

    Wires up in Phase 2 to ``thermofooty.weather`` (vendored from
    ThermoStrife v0.1.1).
    """
    raise NotImplementedError(
        "resolve_event_anomaly is a Phase-2 stub; the weather cascade "
        "vendoring lands alongside the first ingestion pass per the "
        "ThermoFooty dev plan (~/PyProjects/ThermoFooty_DEV_PLAN.md)."
    )
