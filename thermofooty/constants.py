# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — constants                                         ║
# ║  « Wong palette, semantic colours, figure defaults »             ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Central configuration for ThermoFooty visualisations and       ║
# ║  type aliases.  Paths live in thermofooty.config (they are       ║
# ║  env-var-driven); this module is for stable lab conventions      ║
# ║  that don't change per machine.                                  ║
# ║                                                                  ║
# ║  Wong (2011) colourblind-safe palette throughout, with semantic ║
# ║  mappings to the pre-registered hypothesis families (league /    ║
# ║  tournament / dose-response).                                    ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Shared constants: colour palette, figure defaults, type aliases."""

from __future__ import annotations

from typing import TypeAlias

# ┌────────────────────────────────────────────────────────────┐
# │ Wong (2011) palette  « colourblind-safe base colours »     │
# └────────────────────────────────────────────────────────────┘

WONG: dict[str, str] = {
    "black":          "#000000",
    "orange":         "#E69F00",
    "sky_blue":       "#56B4E9",
    "bluish_green":   "#009E73",
    "yellow":         "#F0E442",
    "blue":           "#0072B2",
    "vermilion":      "#D55E00",
    "reddish_purple": "#CC79A7",
}

#: Semantic mapping used across ThermoFooty figures.  Per-hypothesis
#: colour choices below; new figures should pull from here rather than
#: introducing ad-hoc hex values.
SEMANTIC_COLOURS: dict[str, str] = {
    # Outcome streams
    "player_cards":      WONG["vermilion"],
    "crowd_arrests":     WONG["reddish_purple"],
    "fouls_non_card":    WONG["sky_blue"],
    # Stadium types
    "open_air":          WONG["orange"],
    "cooled_indoor":     WONG["bluish_green"],
    "stadium_974":       WONG["yellow"],
    # Panel scopes
    "tier_1":            WONG["blue"],
    "tier_2":            WONG["sky_blue"],
    "tier_3":            WONG["bluish_green"],
    # Battery groupings (for forest plots)
    "league_battery":    WONG["vermilion"],
    "tournament_battery": WONG["blue"],
    "dose_response":     WONG["bluish_green"],
    # Reference
    "null":              WONG["black"],
}

# ┌────────────────────────────────────────────────────────────┐
# │ Figure defaults                                            │
# └────────────────────────────────────────────────────────────┘

FIGURE_DPI: int = 200
FIGURE_SIZE_SINGLE: tuple[float, float] = (7.0, 4.2)
FIGURE_SIZE_DOUBLE: tuple[float, float] = (9.0, 4.5)
FIGURE_SIZE_FOREST: tuple[float, float] = (7.0, 6.0)

# ┌────────────────────────────────────────────────────────────┐
# │ Type aliases                                               │
# └────────────────────────────────────────────────────────────┘

Anomaly: TypeAlias = float        #: temperature anomaly, °C
SubjectId: TypeAlias = str        #: opaque identifier (match, player, …)
Provenance: TypeAlias = str       #: weather-cascade tier flag

# ┌────────────────────────────────────────────────────────────┐
# │ Pre-registered analytical constants  « see OSF DOI »       │
# └────────────────────────────────────────────────────────────┘

#: Half-window of the case-crossover baseline (years).  Locked by the
#: pre-registration; do not modify.
BASELINE_HALF_WINDOW_YEARS: int = 5

#: Days to exclude around the event day when building the baseline
#: window (avoids hot-week-contains-event aliasing).
EVENT_BUFFER_DAYS: int = 7

#: Minimum referent rows required for a case to enter a fit.
MIN_REFERENT_ROWS: int = 8

#: Pre-registered altitude cap (m AGL).  Higher-altitude venues are
#: excluded from H1 / H2 / H3 / H4 / H5 primary fits per the OSF
#: pre-registration's altitude exclusion.
ALTITUDE_CAP_M: int = 2000

#: Maximum allowed distance from stadium to nearest weather station (km).
MAX_STATION_DISTANCE_KM: float = 30.0

#: Maximum allowed time delta between kickoff and weather observation
#: (hours).
MAX_KICKOFF_OBSERVATION_DELTA_HOURS: float = 5.0

#: Minimum baseline-window observations required for an anomaly to be
#: computed; mirrors the ThermoStrife v0.1.1 cascade convention.
MIN_BASELINE_DAYS: int = 20
