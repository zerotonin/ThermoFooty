# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — sources/fbref_reconcile                           ║
# ║  « cross-source sanity check: fbref vs football-data.co.uk »     ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Phase 3d: confirms that fbref's per-card events sum to the      ║
# ║  same per-side card aggregates football-data.co.uk publishes.    ║
# ║  Any large mismatch flags either a scraper bug, a fbref edit     ║
# ║  log entry, or a football-data.co.uk transcription error — all   ║
# ║  three benefit from being surfaced before the analysis runs.     ║
# ║                                                                  ║
# ║  Tolerance is loose by default (within 1 card per side per       ║
# ║  match) because the two sources occasionally disagree on the     ║
# ║  late-90s seasons: football-data.co.uk's yellow column sometimes ║
# ║  excludes second-yellow reds, fbref's lineup table excludes      ║
# ║  yellows shown to subs not yet on the pitch, etc.  Real bugs     ║
# ║  show up as systematic biases > 1 card per side.                 ║
# ╚══════════════════════════════════════════════════════════════════╝
"""fbref vs football-data.co.uk per-match card-count reconciliation."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class MatchMismatch:
    """One match where fbref-sum and football-data card_count diverge."""

    match_id: int
    league_short_code: str
    season: str
    match_date: str
    home_name: str
    away_name: str
    fd_card_home: int
    fd_card_away: int
    fbref_card_home: int
    fbref_card_away: int

    @property
    def delta_home(self) -> int:
        return self.fbref_card_home - self.fd_card_home

    @property
    def delta_away(self) -> int:
        return self.fbref_card_away - self.fd_card_away


@dataclass(frozen=True)
class LeagueSeasonReconciliation:
    """Per-(league, season) reconciliation summary."""

    league_short_code: str
    season: str
    n_matches_with_both_sources: int
    n_perfect_match: int
    n_within_tolerance: int      # |delta| <= tolerance per side
    n_over_tolerance: int
    mean_abs_delta_home: float
    mean_abs_delta_away: float


# ─────────────────────────────────────────────────────────────────
#  Per-match diff query  « joins matches × fbref-card-sum view »
# ─────────────────────────────────────────────────────────────────


_DIFF_SQL = """
WITH fbref_per_match AS (
    SELECT
        c.match_id,
        sum(CASE WHEN l.is_home = 1 THEN 1 ELSE 0 END) AS fbref_home,
        sum(CASE WHEN l.is_home = 0 THEN 1 ELSE 0 END) AS fbref_away
    FROM cards c
    JOIN lineups l ON l.lineup_id = c.lineup_id
    WHERE c.source = 'fbref'
    GROUP BY c.match_id
)
SELECT
    m.match_id, l.short_code, m.season, m.match_date,
    ch.name AS home_name, ca.name AS away_name,
    m.card_count_home, m.card_count_away,
    COALESCE(f.fbref_home, 0) AS fbref_home,
    COALESCE(f.fbref_away, 0) AS fbref_away
FROM matches m
JOIN leagues l ON l.league_id = m.league_id
JOIN clubs ch ON ch.club_id = m.home_club_id
JOIN clubs ca ON ca.club_id = m.away_club_id
JOIN fbref_per_match f ON f.match_id = m.match_id
WHERE m.card_count_home IS NOT NULL
  AND m.card_count_away IS NOT NULL
"""


def per_match_mismatches(
    conn: sqlite3.Connection,
    *,
    tolerance: int = 1,
) -> list[MatchMismatch]:
    """Return every match where |fbref - football-data| > tolerance per side.

    Tolerance applies independently to home and away sides; a match
    qualifies as a mismatch if either side's delta exceeds the
    threshold.  ``tolerance=1`` accepts the small upstream-quirk
    disagreements (off-by-one yellow on a sub) and surfaces only
    systematic bias.
    """
    cur = conn.execute(_DIFF_SQL)
    out: list[MatchMismatch] = []
    for row in cur.fetchall():
        delta_h = int(row[8]) - int(row[6])
        delta_a = int(row[9]) - int(row[7])
        if max(abs(delta_h), abs(delta_a)) <= tolerance:
            continue
        out.append(
            MatchMismatch(
                match_id=int(row[0]),
                league_short_code=str(row[1]),
                season=str(row[2]),
                match_date=str(row[3]),
                home_name=str(row[4]),
                away_name=str(row[5]),
                fd_card_home=int(row[6]),
                fd_card_away=int(row[7]),
                fbref_card_home=int(row[8]),
                fbref_card_away=int(row[9]),
            )
        )
    return out


def by_league_season(
    conn: sqlite3.Connection,
    *,
    tolerance: int = 1,
) -> list[LeagueSeasonReconciliation]:
    """Per-(league, season) summary of the per-match reconciliation."""
    cur = conn.execute(_DIFF_SQL)
    # group rows by (league, season)
    buckets: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for row in cur.fetchall():
        key = (str(row[1]), str(row[2]))
        delta_h = int(row[8]) - int(row[6])
        delta_a = int(row[9]) - int(row[7])
        buckets.setdefault(key, []).append((delta_h, delta_a))

    out: list[LeagueSeasonReconciliation] = []
    for (league, season), deltas in sorted(buckets.items()):
        n_total = len(deltas)
        n_perfect = sum(1 for d in deltas if d[0] == 0 and d[1] == 0)
        n_within = sum(
            1 for d in deltas
            if max(abs(d[0]), abs(d[1])) <= tolerance
        )
        n_over = n_total - n_within
        mean_abs_h = sum(abs(d[0]) for d in deltas) / n_total
        mean_abs_a = sum(abs(d[1]) for d in deltas) / n_total
        out.append(
            LeagueSeasonReconciliation(
                league_short_code=league,
                season=season,
                n_matches_with_both_sources=n_total,
                n_perfect_match=n_perfect,
                n_within_tolerance=n_within,
                n_over_tolerance=n_over,
                mean_abs_delta_home=mean_abs_h,
                mean_abs_delta_away=mean_abs_a,
            )
        )
    return out
