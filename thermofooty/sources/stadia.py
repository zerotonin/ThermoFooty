# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — sources/stadia                                    ║
# ║  « committed seed CSVs → SQLite stadia + clubs + history »       ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Stadium coordinates and the club-to-stadium history live in     ║
# ║  db/seed/ (version-controlled lab knowledge, not bulk data).     ║
# ║  This module loads them into SQLite at the start of every        ║
# ║  ingestion run and exposes a resolver that maps                  ║
# ║  (club_canonical, season) → stadium_id for the match-row writer  ║
# ║  to use.                                                         ║
# ║                                                                  ║
# ║  All inserts are idempotent — re-running an ingestion pass does  ║
# ║  not duplicate stadia or club rows.                              ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Stadium + club-history seed-data loader for ThermoFooty."""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from thermofooty.config import REPO_ROOT

# ─────────────────────────────────────────────────────────────────
#  Seed-CSV paths  « committed under db/seed/ »
# ─────────────────────────────────────────────────────────────────

SEED_DIR: Path = REPO_ROOT / "db" / "seed"
ENGLISH_STADIA_CSV: Path = SEED_DIR / "english_stadia.csv"
ENGLISH_HISTORY_CSV: Path = SEED_DIR / "english_club_stadium_history.csv"


# ─────────────────────────────────────────────────────────────────
#  Resolver dataclass
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ClubStadiumPeriod:
    """One slice of a club's stadium history."""

    club_canonical: str
    aliases: tuple[str, ...]
    stadium_name: str
    first_season: str            # 'YYYY-YY' like '1992-93'
    last_season: str | None      # None = open-ended (still using this stadium)


# ─────────────────────────────────────────────────────────────────
#  Season comparison  « football seasons sort by start year »
# ─────────────────────────────────────────────────────────────────


def _season_start_year(season: str) -> int:
    """Return the calendar year the season started in ('1999-2000' → 1999)."""
    return int(season.split("-", 1)[0])


def _season_in_range(season: str, first: str, last: str | None) -> bool:
    """True if ``season`` falls within ``[first, last]`` (last=None = open)."""
    s = _season_start_year(season)
    if s < _season_start_year(first):
        return False
    return not (last is not None and s > _season_start_year(last))


# ─────────────────────────────────────────────────────────────────
#  Country bootstrap  « one row per primary-panel country »
# ─────────────────────────────────────────────────────────────────


def _ensure_country(conn: sqlite3.Connection, iso_alpha2: str, name: str) -> int:
    """Insert country if absent; return its country_id."""
    cur = conn.execute(
        "SELECT country_id FROM countries WHERE iso_alpha2 = ?", (iso_alpha2,),
    )
    row = cur.fetchone()
    if row is not None:
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO countries (iso_alpha2, name) VALUES (?, ?)",
        (iso_alpha2, name),
    )
    return int(cur.lastrowid)


# ─────────────────────────────────────────────────────────────────
#  Stadia loader  « idempotent upsert keyed on (name, country) »
# ─────────────────────────────────────────────────────────────────


def load_stadia(
    conn: sqlite3.Connection,
    stadia_csv: Path = ENGLISH_STADIA_CSV,
    *,
    country_iso: str = "EN",
    country_name: str = "England",
) -> tuple[dict[str, int], int]:
    """Load the stadia seed CSV into SQLite.

    Returns ``(stadium_name_to_id, country_id)`` so callers can wire
    league + match rows without re-querying ``countries``.  Idempotent:
    re-runs do not duplicate rows.
    """
    if not stadia_csv.exists():
        raise FileNotFoundError(
            f"stadia seed CSV missing: {stadia_csv}.  Expected the "
            f"db/seed/ directory to ship with the repo."
        )
    country_id = _ensure_country(conn, country_iso, country_name)

    name_to_id: dict[str, int] = {}
    with stadia_csv.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            name = row["stadium_name"]
            cur = conn.execute(
                "SELECT stadium_id FROM stadia WHERE name = ? AND country_id = ?",
                (name, country_id),
            )
            existing = cur.fetchone()
            if existing is not None:
                name_to_id[name] = int(existing[0])
                continue
            cur = conn.execute(
                """
                INSERT INTO stadia (
                    name, country_id, city, latitude, longitude, altitude_m,
                    has_roof, qatar2022_cooled, nearest_icao,
                    nearest_icao_distance_km, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    name, country_id, row["city"] or None,
                    float(row["latitude"]), float(row["longitude"]),
                    float(row["altitude_m"]) if row["altitude_m"] else None,
                    int(row["has_roof"]),
                    row["nearest_icao"] or None,
                    float(row["nearest_icao_distance_km"])
                    if row["nearest_icao_distance_km"] else None,
                    row["notes"] or None,
                ),
            )
            name_to_id[name] = int(cur.lastrowid)
    return name_to_id, country_id


# ─────────────────────────────────────────────────────────────────
#  Club + history loader
# ─────────────────────────────────────────────────────────────────


def load_club_stadium_history(
    conn: sqlite3.Connection,
    stadium_name_to_id: dict[str, int],
    history_csv: Path = ENGLISH_HISTORY_CSV,
    *,
    country_iso: str = "EN",
    country_name: str = "England",
) -> tuple[dict[str, int], list[ClubStadiumPeriod]]:
    """Load the club-to-stadium history CSV.

    Returns ``(alias_to_club_id, periods)`` — the alias lookup powers
    football-data.co.uk's loose club-name resolution and ``periods``
    drives the (club, season) → stadium_id resolver.
    """
    if not history_csv.exists():
        raise FileNotFoundError(
            f"club history seed CSV missing: {history_csv}."
        )
    country_id = _ensure_country(conn, country_iso, country_name)

    alias_to_club_id: dict[str, int] = {}
    periods: list[ClubStadiumPeriod] = []
    with history_csv.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            canonical = row["club_canonical"]
            aliases = tuple(
                a.strip() for a in row["club_aliases"].split("|") if a.strip()
            )
            # Ensure the club row exists
            cur = conn.execute(
                "SELECT club_id FROM clubs WHERE name = ? AND country_id = ?",
                (canonical, country_id),
            )
            existing = cur.fetchone()
            if existing is not None:
                club_id = int(existing[0])
            else:
                cur = conn.execute(
                    "INSERT INTO clubs (name, country_id, short_name) VALUES (?, ?, ?)",
                    (canonical, country_id, aliases[0] if aliases else None),
                )
                club_id = int(cur.lastrowid)
            for alias in (canonical, *aliases):
                alias_to_club_id[alias.lower()] = club_id

            if row["stadium_name"] not in stadium_name_to_id:
                raise ValueError(
                    f"Club history references unknown stadium "
                    f"'{row['stadium_name']}' (club '{canonical}').  "
                    f"Add it to db/seed/epl_stadia.csv first."
                )
            periods.append(
                ClubStadiumPeriod(
                    club_canonical=canonical,
                    aliases=aliases,
                    stadium_name=row["stadium_name"],
                    first_season=row["first_season"],
                    last_season=row["last_season"] or None,
                )
            )
    return alias_to_club_id, periods


# ─────────────────────────────────────────────────────────────────
#  Resolver  « (club, season) → stadium_id »
# ─────────────────────────────────────────────────────────────────


def build_stadium_resolver(
    periods: list[ClubStadiumPeriod],
    stadium_name_to_id: dict[str, int],
):
    """Return a closure ``(club_canonical, season) → stadium_id | None``.

    Picks the period whose ``[first_season, last_season]`` interval
    contains ``season`` (case-insensitive on the club name).  Returns
    ``None`` if no period matches — the caller decides whether to
    skip the match or fail loudly.
    """
    by_club: dict[str, list[ClubStadiumPeriod]] = {}
    for period in periods:
        by_club.setdefault(period.club_canonical.lower(), []).append(period)

    def resolve(club_canonical: str, season: str) -> int | None:
        candidates = by_club.get(club_canonical.lower(), [])
        for period in candidates:
            if _season_in_range(season, period.first_season, period.last_season):
                return stadium_name_to_id[period.stadium_name]
        return None

    return resolve
