# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — viz                                               ║
# ║  « Wong-palette figures, SVG + PNG + CSV triple-output »         ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Wong-palette figure helpers.

Phase 1 (scaffold): import-only stub.  Phase 7 lands the SOTA viz
inventory mirroring the ThermoStrife pipeline (raincloud, null
density, forest plot, superposed epoch, warming-stripes timeline).
All figures triple-output (SVG + PNG + CSV) per the lab convention.

This module forces ``MPLBACKEND=Agg`` at import time to stay headless-
safe on CI runners and on the thermostrife conda env that ships PyQt5.
"""

from __future__ import annotations

import os

# Set before pyplot is touched anywhere downstream.
os.environ.setdefault("MPLBACKEND", "Agg")
import matplotlib  # noqa: E402

matplotlib.use("Agg", force=True)
