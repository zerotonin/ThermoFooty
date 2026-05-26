# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — sources/football_data_uk                          ║
# ║  « season-CSV ingestion from football-data.co.uk »               ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Downloads one CSV per (league, season) from                     ║
# ║  https://www.football-data.co.uk/mmz4281/<SSEE>/<LEAGUE>.csv,    ║
# ║  parses the rows, and upserts into the SQLite `matches` table.   ║
# ║                                                                  ║
# ║  All inserts are idempotent (UNIQUE constraint on                ║
# ║  (stadium_id, match_date, home_club_id, away_club_id) handles    ║
# ║  re-runs).  Every download adds one row to `data_provenance`     ║
# ║  carrying SHA-256 + row count so the ingestion pass is fully     ║
# ║  audit-traceable.                                                ║
# ║                                                                  ║
# ║  Card columns (HY / AY / HR / AR) appear from 1995-96 onwards;   ║
# ║  pre-1995-96 rows write card_count_home / card_count_away as     ║
# ║  NULL and the data_tier is 'B' across all football-data.co.uk    ║
# ║  rows (match-level only).  fbref ingestion in Phase 3 supersedes ║
# ║  these aggregates with per-player card events.                   ║
# ╚══════════════════════════════════════════════════════════════════╝
"""football-data.co.uk season-CSV ingestion."""

from __future__ import annotations

import csv
import hashlib
import io
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from thermofooty.config import RAW_FOOTBALL_DATA_UK

# ─────────────────────────────────────────────────────────────────
#  URL + path conventions
# ─────────────────────────────────────────────────────────────────

BASE_URL: str = "https://www.football-data.co.uk/mmz4281"
LEAGUE_CODES: dict[str, str] = {
    "EN_PREM":  "E0",   # English Premier League
    "EN_CHAMP": "E1",   # Championship
    "EN_L1":    "E2",   # League One
    "EN_L2":    "E3",   # League Two
}

#: First season football-data.co.uk publishes for each league.
#: Championship + L1 cover the pre-2004 First Division / Second Division
#: era under their modern names — the league hierarchy is unchanged and
#: data continuity is preserved through the rebrand.
FIRST_SEASON: dict[str, str] = {
    "EN_PREM":  "1993-94",
    "EN_CHAMP": "1993-94",
    "EN_L1":    "1993-94",
    "EN_L2":    "1993-94",
}

#: Display name + tier for each league short_code.  Used by the CLI
#: ingestion script when bootstrapping the `leagues` row.
LEAGUE_METADATA: dict[str, tuple[str, int]] = {
    "EN_PREM":  ("Premier League", 1),
    "EN_CHAMP": ("Championship", 2),
    "EN_L1":    ("League One", 3),
    "EN_L2":    ("League Two", 4),
}


def season_to_url_token(season: str) -> str:
    """Convert '1993-94' to '9394' / '1999-2000' to '9900' / '2023-24' to '2324'."""
    start, end = season.split("-")
    s2 = start[-2:]
    # End year may be 2-digit ('94') or 4-digit ('2000') — take the last two
    e2 = end[-2:]
    return f"{s2}{e2}"


def season_url(league_short_code: str, season: str) -> str:
    """Build the canonical football-data.co.uk URL for one (league, season)."""
    if league_short_code not in LEAGUE_CODES:
        raise KeyError(
            f"Unknown league short_code '{league_short_code}'.  "
            f"Known: {sorted(LEAGUE_CODES)}"
        )
    return f"{BASE_URL}/{season_to_url_token(season)}/{LEAGUE_CODES[league_short_code]}.csv"


def season_cache_path(league_short_code: str, season: str) -> Path:
    """Return the on-disk cache path for one (league, season) CSV download."""
    fname = f"{league_short_code.lower()}_{season}.csv"
    return RAW_FOOTBALL_DATA_UK / fname


# ─────────────────────────────────────────────────────────────────
#  Download  « caches under data/raw/football_data_uk/ »
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DownloadResult:
    """One season CSV fetched (or pulled from cache)."""

    league_short_code: str
    season: str
    url: str
    csv_path: Path
    sha256: str
    from_cache: bool


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def download_season_csv(
    league_short_code: str,
    season: str,
    *,
    use_cache: bool = True,
    timeout_s: float = 60.0,
) -> DownloadResult:
    """Fetch one (league, season) CSV from football-data.co.uk.

    Cached at ``$THERMOFOOTY_DATA_ROOT/raw/football_data_uk/`` so
    repeated parses don't re-hit the upstream.  Pass ``use_cache=False``
    to force a re-fetch (rare; only when the season is still live and
    rows may have been added upstream).
    """
    url = season_url(league_short_code, season)
    path = season_cache_path(league_short_code, season)
    if use_cache and path.exists():
        payload = path.read_bytes()
        return DownloadResult(
            league_short_code=league_short_code,
            season=season,
            url=url,
            csv_path=path,
            sha256=_sha256_bytes(payload),
            from_cache=True,
        )
    import requests
    response = requests.get(url, timeout=timeout_s)
    response.raise_for_status()
    payload = response.content
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return DownloadResult(
        league_short_code=league_short_code,
        season=season,
        url=url,
        csv_path=path,
        sha256=_sha256_bytes(payload),
        from_cache=False,
    )


# ─────────────────────────────────────────────────────────────────
#  Parser
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParsedMatch:
    """One row of football-data.co.uk parsed and ready for INSERT."""

    date_iso: str               # 'YYYY-MM-DD'
    home_team: str
    away_team: str
    ft_home_goals: int | None
    ft_away_goals: int | None
    ht_home_goals: int | None
    ht_away_goals: int | None
    card_count_home: int | None
    card_count_away: int | None
    referee: str | None


def _parse_date(raw: str) -> str:
    """Coerce 'DD/MM/YY' or 'DD/MM/YYYY' to ISO 'YYYY-MM-DD'."""
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unparseable football-data.co.uk date: {raw!r}")


def _coerce_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        # Some early-season cells are floats like '1.0'
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _coerce_cards(yellow: str | None, red: str | None) -> int | None:
    """Combined yellow+red total, NULL if both source cells are blank."""
    y = _coerce_int(yellow)
    r = _coerce_int(red)
    if y is None and r is None:
        return None
    return (y or 0) + (r or 0)


def parse_season_csv(csv_path: Path) -> list[ParsedMatch]:
    """Parse a downloaded football-data.co.uk CSV into ParsedMatch rows.

    Skips rows where required columns (Date, HomeTeam, AwayTeam) are
    missing — these appear as trailing blank lines in some seasons.
    """
    # Football-data.co.uk ships some files as cp1252 (the older ones with
    # accented manager names); we try utf-8 first and fall back.
    for encoding in ("utf-8", "cp1252"):
        try:
            text = csv_path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnicodeDecodeError(  # type: ignore[call-arg]
            "utf-8", b"", 0, 1, f"failed to decode {csv_path} as utf-8 or cp1252",
        )

    matches: list[ParsedMatch] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        date_raw = (row.get("Date") or "").strip()
        home = (row.get("HomeTeam") or row.get("HT") or "").strip()
        away = (row.get("AwayTeam") or row.get("AT") or "").strip()
        if not date_raw or not home or not away:
            continue
        try:
            date_iso = _parse_date(date_raw)
        except ValueError:
            continue
        matches.append(
            ParsedMatch(
                date_iso=date_iso,
                home_team=home,
                away_team=away,
                ft_home_goals=_coerce_int(row.get("FTHG")),
                ft_away_goals=_coerce_int(row.get("FTAG")),
                ht_home_goals=_coerce_int(row.get("HTHG")),
                ht_away_goals=_coerce_int(row.get("HTAG")),
                card_count_home=_coerce_cards(row.get("HY"), row.get("HR")),
                card_count_away=_coerce_cards(row.get("AY"), row.get("AR")),
                referee=(row.get("Referee") or "").strip() or None,
            )
        )
    return matches


# ─────────────────────────────────────────────────────────────────
#  League + referee bookkeeping
# ─────────────────────────────────────────────────────────────────


def _ensure_league(
    conn: sqlite3.Connection,
    *,
    country_id: int,
    short_code: str,
    name: str,
    tier: int,
    in_primary_panel: int = 1,
) -> int:
    cur = conn.execute(
        "SELECT league_id FROM leagues WHERE short_code = ?", (short_code,),
    )
    row = cur.fetchone()
    if row is not None:
        return int(row[0])
    cur = conn.execute(
        """
        INSERT INTO leagues (country_id, name, tier, short_code, in_primary_panel)
        VALUES (?, ?, ?, ?, ?)
        """,
        (country_id, name, tier, short_code, in_primary_panel),
    )
    return int(cur.lastrowid)


def _ensure_referee(
    conn: sqlite3.Connection, name: str, country_id: int,
) -> int:
    cur = conn.execute(
        "SELECT referee_id FROM referees WHERE name = ? AND country_id IS ?",
        (name, country_id),
    )
    row = cur.fetchone()
    if row is not None:
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO referees (name, country_id) VALUES (?, ?)",
        (name, country_id),
    )
    return int(cur.lastrowid)


# ─────────────────────────────────────────────────────────────────
#  Upsert
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IngestionStats:
    """Per-season ingestion summary for the rich progress table."""

    league_short_code: str
    season: str
    parsed_rows: int
    inserted_rows: int
    skipped_rows: int           # rows we couldn't resolve a stadium / club for
    from_cache: bool


def upsert_matches(
    conn: sqlite3.Connection,
    *,
    league_id: int,
    season: str,
    country_id: int,
    matches: list[ParsedMatch],
    alias_to_club_id: dict[str, int],
    stadium_resolver,
    source_url: str,
) -> int:
    """Insert parsed matches into ``matches``.  Returns the skipped count.

    Inserted count is computed by the caller as the delta on
    ``conn.total_changes`` so SQLite's INSERT OR IGNORE bookkeeping
    stays the single source of truth.
    """
    skipped = 0
    for m in matches:
        home_id = alias_to_club_id.get(m.home_team.lower())
        away_id = alias_to_club_id.get(m.away_team.lower())
        if home_id is None or away_id is None:
            skipped += 1
            continue
        cur = conn.execute("SELECT name FROM clubs WHERE club_id = ?", (home_id,))
        row = cur.fetchone()
        if row is None:
            skipped += 1
            continue
        canonical = row[0]
        stadium_id = stadium_resolver(canonical, season)
        if stadium_id is None:
            skipped += 1
            continue
        referee_id = None
        if m.referee:
            referee_id = _ensure_referee(conn, m.referee, country_id)
        conn.execute(
            """
            INSERT OR IGNORE INTO matches (
                league_id, season, match_date, home_club_id, away_club_id,
                stadium_id, referee_id, ft_home_goals, ft_away_goals,
                ht_home_goals, ht_away_goals, card_count_home,
                card_count_away, data_tier, source_primary, source_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'B',
                      'football_data_uk', ?)
            """,
            (
                league_id, season, m.date_iso, home_id, away_id,
                stadium_id, referee_id, m.ft_home_goals, m.ft_away_goals,
                m.ht_home_goals, m.ht_away_goals, m.card_count_home,
                m.card_count_away, source_url,
            ),
        )
    return skipped


def record_provenance(
    conn: sqlite3.Connection,
    *,
    download: DownloadResult,
    n_rows_pulled: int,
    notes: str = "",
) -> None:
    """Insert one row into ``data_provenance`` for the ingestion run."""
    accessed_at = datetime.now(UTC).isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO data_provenance (
            source, accessed_at, n_rows_pulled, sha256_payload, notes
        ) VALUES ('football_data_uk', ?, ?, ?, ?)
        """,
        (accessed_at, n_rows_pulled, download.sha256, notes or download.url),
    )


# ─────────────────────────────────────────────────────────────────
#  End-to-end orchestration  « called by the CLI script »
# ─────────────────────────────────────────────────────────────────


def ingest_season(
    conn: sqlite3.Connection,
    *,
    league_short_code: str,
    season: str,
    country_id: int,
    league_id: int,
    alias_to_club_id: dict[str, int],
    stadium_resolver,
    use_cache: bool = True,
) -> IngestionStats:
    """Download + parse + upsert + provenance for one (league, season)."""
    download = download_season_csv(
        league_short_code, season, use_cache=use_cache,
    )
    parsed = parse_season_csv(download.csv_path)
    before = conn.total_changes
    skipped = upsert_matches(
        conn,
        league_id=league_id,
        season=season,
        country_id=country_id,
        matches=parsed,
        alias_to_club_id=alias_to_club_id,
        stadium_resolver=stadium_resolver,
        source_url=download.url,
    )
    inserted_delta = conn.total_changes - before
    record_provenance(
        conn, download=download, n_rows_pulled=len(parsed),
        notes=(
            f"{league_short_code} {season}: parsed={len(parsed)} "
            f"inserted={inserted_delta} skipped={skipped} "
            f"({'cached' if download.from_cache else 'fetched'})"
        ),
    )
    return IngestionStats(
        league_short_code=league_short_code,
        season=season,
        parsed_rows=len(parsed),
        inserted_rows=inserted_delta,
        skipped_rows=skipped,
        from_cache=download.from_cache,
    )


def all_seasons_for(league_short_code: str, through_season: str) -> list[str]:
    """Inclusive list of seasons from ``FIRST_SEASON[league]`` to ``through_season``."""
    if league_short_code not in FIRST_SEASON:
        raise KeyError(
            f"No FIRST_SEASON entry for {league_short_code!r}.  "
            f"Add it before requesting an open-ended season range."
        )
    first = _season_start_year(FIRST_SEASON[league_short_code])
    last = _season_start_year(through_season)
    return [_format_season(y) for y in range(first, last + 1)]


def _season_start_year(season: str) -> int:
    return int(season.split("-", 1)[0])


def _format_season(start_year: int) -> str:
    end = start_year + 1
    return f"{start_year}-{str(end)[-2:]}" if end != 2000 else f"{start_year}-2000"
