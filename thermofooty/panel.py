# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — panel                                             ║
# ║  « one row per (match, side) joined to weather and outcomes »    ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Materialises the analysis_panel DataFrame from SQLite:          ║
# ║  matches join stadia join weather, exploded into one row per     ║
# ║  match-side (home + away).  Each row carries the side's club,    ║
# ║  the opponent, the stadium-day Tmax + anomaly + provenance, and  ║
# ║  the side's per-match card aggregates from football-data.co.uk.  ║
# ║                                                                  ║
# ║  Outcome columns are intentionally minimal until Phase 3 fbref   ║
# ║  ingestion lands per-card reason codes:                          ║
# ║    n_cards_total       — yellows + reds, side-specific           ║
# ║    n_reds_total        — reds only (NA until Phase 3)            ║
# ║    side_received_card  — binary, =1 if n_cards_total >= 1        ║
# ║    side_received_red   — placeholder (NA until Phase 3)          ║
# ║                                                                  ║
# ║  Filters applied at materialisation time:                        ║
# ║    - league.in_primary_panel = 1                                 ║
# ║    - weather row exists                                          ║
# ║    - source_tier NOT IN ('unverifiable', 'excluded_altitude')    ║
# ║    - card_count_home / card_count_away IS NOT NULL               ║
# ║      (drops pre-1995-96 EPL rows that lack card columns)         ║
# ║                                                                  ║
# ║  Writes the parquet under                                        ║
# ║  $THERMOFOOTY_DATA_ROOT/derived/analysis_panel.parquet on        ║
# ║  request — but the in-memory DataFrame is the canonical return   ║
# ║  value so the inference layer doesn't have to round-trip through ║
# ║  disk during testing.                                            ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Analysis-panel materialiser (matches join weather, exploded per side)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from thermofooty.config import DERIVED_DIR

# ─────────────────────────────────────────────────────────────────
#  Canonical column ordering for analysis_panel
# ─────────────────────────────────────────────────────────────────

PANEL_COLUMNS: list[str] = [
    "match_id", "league_short_code", "season", "match_date", "kickoff_time",
    "side", "is_home",
    "club_id", "club_name", "opponent_club_id", "opponent_name",
    "stadium_id", "stadium_name", "latitude", "longitude", "altitude_m",
    "tmax_obs_c", "tmax_anomaly_c", "baseline_mean_c", "baseline_std_c",
    "baseline_n_days", "source_tier", "source_id",
    "n_cards_total", "n_reds_total", "side_received_red", "side_received_card",
]


# ─────────────────────────────────────────────────────────────────
#  SQL  « joins all the way through; explode-per-side is done in pandas »
# ─────────────────────────────────────────────────────────────────


_PANEL_SQL = """
SELECT
    m.match_id,
    l.short_code              AS league_short_code,
    m.season,
    m.match_date,
    m.kickoff_time,
    m.home_club_id,
    ch.name                   AS home_name,
    m.away_club_id,
    ca.name                   AS away_name,
    m.stadium_id,
    s.name                    AS stadium_name,
    s.latitude,
    s.longitude,
    s.altitude_m,
    m.card_count_home,
    m.card_count_away,
    w.tmax_obs_c,
    w.tmax_anomaly_c,
    w.baseline_mean_c,
    w.baseline_std_c,
    w.baseline_n_days,
    w.source_tier,
    w.source_id
FROM matches m
JOIN leagues l ON l.league_id = m.league_id
JOIN clubs ch ON ch.club_id  = m.home_club_id
JOIN clubs ca ON ca.club_id  = m.away_club_id
JOIN stadia s ON s.stadium_id = m.stadium_id
JOIN weather w
       ON w.stadium_id       = m.stadium_id
      AND w.observation_date = m.match_date
WHERE l.in_primary_panel = 1
  AND w.source_tier IS NOT NULL
  AND w.source_tier NOT IN ('unverifiable', 'excluded_altitude')
  AND m.card_count_home IS NOT NULL
  AND m.card_count_away IS NOT NULL
ORDER BY m.match_date ASC, m.match_id ASC
"""


def _load_joined_matches(conn: sqlite3.Connection) -> pd.DataFrame:
    """Run the matches join weather query and return one row per match."""
    return pd.read_sql_query(_PANEL_SQL, conn)


# ─────────────────────────────────────────────────────────────────
#  Per-side explosion  « 1 row per match -> 2 rows (home + away) »
# ─────────────────────────────────────────────────────────────────


def _explode_per_side(matches: pd.DataFrame) -> pd.DataFrame:
    """Stack home + away rows.  Each match contributes 2 rows to the panel."""
    base_cols = [
        "match_id", "league_short_code", "season", "match_date", "kickoff_time",
        "stadium_id", "stadium_name", "latitude", "longitude", "altitude_m",
        "tmax_obs_c", "tmax_anomaly_c", "baseline_mean_c", "baseline_std_c",
        "baseline_n_days", "source_tier", "source_id",
    ]
    home = matches[base_cols].copy()
    home["side"] = "home"
    home["is_home"] = 1
    home["club_id"] = matches["home_club_id"]
    home["club_name"] = matches["home_name"]
    home["opponent_club_id"] = matches["away_club_id"]
    home["opponent_name"] = matches["away_name"]
    # football-data.co.uk's HY+HR aggregate (home yellows + reds).  When
    # Phase 3 fbref ingestion lands, this column gets replaced by the
    # per-card sum from the cards table.
    home["n_cards_total"] = matches["card_count_home"].astype("Int64")
    # n_reds_total left as NA until per-card reason codes land in Phase 3.
    home["n_reds_total"] = pd.NA

    away = matches[base_cols].copy()
    away["side"] = "away"
    away["is_home"] = 0
    away["club_id"] = matches["away_club_id"]
    away["club_name"] = matches["away_name"]
    away["opponent_club_id"] = matches["home_club_id"]
    away["opponent_name"] = matches["home_name"]
    away["n_cards_total"] = matches["card_count_away"].astype("Int64")
    away["n_reds_total"] = pd.NA

    panel = pd.concat([home, away], ignore_index=True)
    panel["side_received_card"] = (panel["n_cards_total"] >= 1).astype("Int64")
    # side_received_red stays NA — the Phase 3 fbref ingestion populates it
    # alongside per-card reason codes.  Phase 5a uses side_received_card.
    panel["side_received_red"] = pd.NA

    return panel[PANEL_COLUMNS]


# ─────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────


def materialise_analysis_panel(
    conn: sqlite3.Connection,
    *,
    write_parquet: bool = False,
    output_path: Path | None = None,
) -> pd.DataFrame:
    """Build the analysis_panel DataFrame from SQLite.

    Args:
        conn: Open SQLite connection (foreign keys enforced).
        write_parquet: If True, also persist the panel under
            ``DERIVED_DIR/analysis_panel.parquet``.
        output_path: Override the parquet location (caller-supplied
            absolute path).  Ignored when ``write_parquet=False``.

    Returns:
        DataFrame with the column ordering documented in
        ``PANEL_COLUMNS`` — one row per (match, side), filtered to the
        primary-panel leagues with resolved weather and non-null card
        aggregates.
    """
    matches = _load_joined_matches(conn)
    panel = _explode_per_side(matches)

    if write_parquet:
        path = output_path if output_path is not None else (
            DERIVED_DIR / "analysis_panel.parquet"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        panel.to_parquet(path, index=False)

    return panel
