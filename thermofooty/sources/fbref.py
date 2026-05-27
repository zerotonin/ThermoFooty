# ╔══════════════════════════════════════════════════════════════════╗
# ║  ThermoFooty — sources/fbref                                     ║
# ║  « rate-limited HTML scraper for per-match lineups + cards »     ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Phase 3a (this revision): foundation layer only.                ║
# ║    - rate-limited HTTP client (1 req per N seconds, configurable)║
# ║    - per-URL HTML cache under                                    ║
# ║      $THERMOFOOTY_DATA_ROOT/raw/fbref_html/ (RAW, not cache —    ║
# ║      once fbref takes a page down we can't get it back)          ║
# ║    - season-schedule parser: extract (match_date, home_team,     ║
# ║      away_team, fbref_match_id, match_report_url) from           ║
# ║      a competition-season schedule page                          ║
# ║                                                                  ║
# ║  Phase 3b lands the match-report parser (lineups + cards),       ║
# ║  Phase 3c lands the ingestion CLI + SQLite upserts, Phase 3d     ║
# ║  lands the reconciliation pass against the football-data.co.uk  ║
# ║  match-level card aggregates.                                    ║
# ║                                                                  ║
# ║  Rate-limit default = 3 s between requests, matching the         ║
# ║  worldfootballR community convention.  fbref's published policy  ║
# ║  is "be reasonable"; 1 req per 3 s has held up across years of   ║
# ║  academic use in the soccer-analytics community.                 ║
# ║                                                                  ║
# ║  Network-touching only in the HTTP layer.  All parsers operate  ║
# ║  on bytes / HTML strings so the test suite can drive them with   ║
# ║  committed fixtures and never hit the network.                   ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Rate-limited fbref scraper foundation: HTTP + cache + schedule parser."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from thermofooty.config import RAW_FBREF_HTML

# ─────────────────────────────────────────────────────────────────
#  URL conventions
# ─────────────────────────────────────────────────────────────────

FBREF_BASE: str = "https://fbref.com"

#: Per-competition fbref IDs.  These are stable identifiers fbref
#: uses in its URL structure and rarely change.
COMP_IDS: dict[str, int] = {
    "EN_PREM":  9,   # English Premier League
    "EN_CHAMP": 10,  # English Championship
    "EN_L1":    15,  # English League One
    "EN_L2":    16,  # English League Two
}

#: Per-competition slugs fbref uses in URL paths (the "scores-and-fixtures"
#: trailing component).  Slugs follow fbref's own URL conventions.
COMP_SLUGS: dict[str, str] = {
    "EN_PREM":  "Premier-League",
    "EN_CHAMP": "Championship",
    "EN_L1":    "League-One",
    "EN_L2":    "League-Two",
}


def schedule_url(league_short_code: str, season: str) -> str:
    """Build the canonical fbref season-schedule URL for one (league, season).

    Seasons are passed in the project's ``YYYY-YY`` form (e.g. ``"2023-24"``);
    fbref expects ``YYYY-YYYY`` (e.g. ``"2023-2024"``) so we widen the
    two-digit suffix back to four.
    """
    if league_short_code not in COMP_IDS:
        raise KeyError(
            f"unknown league short_code {league_short_code!r}; "
            f"known: {sorted(COMP_IDS)}"
        )
    start, end = season.split("-")
    if len(end) == 2:
        # Widen '24' -> '2024' using the start year's century; bump the
        # century when the two-digit suffix has rolled over (e.g. '99-00').
        same_century = int(end) >= int(start[-2:])
        century = int(start[:2]) if same_century else int(start[:2]) + 1
        end = f"{century:02d}{end}"
    fbref_season = f"{start}-{end}"
    slug = COMP_SLUGS[league_short_code]
    return (
        f"{FBREF_BASE}/en/comps/{COMP_IDS[league_short_code]}/"
        f"{fbref_season}/schedule/{fbref_season}-{slug}-Scores-and-Fixtures"
    )


# ─────────────────────────────────────────────────────────────────
#  HTML cache  « cache key = sha1(url), file = raw HTML bytes »
# ─────────────────────────────────────────────────────────────────


def cache_path_for(url: str, *, cache_dir: Path | None = None) -> Path:
    """Return the on-disk cache path for one URL.

    Filename is a short SHA-1 of the URL plus a human-readable trailing
    fragment so a directory listing tells you what's in the cache
    without resolving hashes.  The actual cache key is the SHA-1; the
    fragment is decorative.

    ``cache_dir`` defaults to the module-level ``RAW_FBREF_HTML``,
    resolved at call time so a monkey-patched value (in tests) takes
    effect without re-importing.
    """
    if cache_dir is None:
        cache_dir = RAW_FBREF_HTML
    sha = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    parsed = urlparse(url)
    tail = parsed.path.rstrip("/").rsplit("/", 1)[-1] or "index"
    tail = re.sub(r"[^A-Za-z0-9_.-]", "_", tail)[:64]
    return cache_dir / f"{sha}_{tail}.html"


# ─────────────────────────────────────────────────────────────────
#  Rate-limited HTTP client
# ─────────────────────────────────────────────────────────────────


@dataclass
class RateLimitedClient:
    """Process-local rate limiter for fbref requests.

    ``min_interval_s`` is the minimum spacing between two outgoing
    requests on this client.  Use one client per process (or per
    worker) so the rate limit is enforced globally.  Re-instantiating
    a client does NOT reset cross-process timing — for that, run in
    a single process or coordinate via a shared lock file.
    """

    min_interval_s: float = 3.0
    user_agent: str = (
        "ThermoFooty/0.1 (https://github.com/zerotonin/ThermoFooty; "
        "academic research; contact via repo issues)"
    )
    timeout_s: float = 30.0
    _last_request_at: float = 0.0

    def _sleep_until_ready(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_at
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)
        self._last_request_at = time.monotonic()

    def fetch(self, url: str) -> bytes:
        """Perform one HTTP GET respecting the rate limit.  Returns raw bytes."""
        import requests
        self._sleep_until_ready()
        response = requests.get(
            url,
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        return response.content


# ─────────────────────────────────────────────────────────────────
#  Cached fetch  « returns bytes, hitting disk first, network second »
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FetchResult:
    """One cached-or-fetched HTML page."""

    url: str
    html_path: Path
    payload: bytes
    from_cache: bool


def fetch_cached(
    url: str,
    *,
    client: RateLimitedClient | None = None,
    cache_dir: Path | None = None,
    refetch: bool = False,
) -> FetchResult:
    """Fetch one URL with on-disk HTML caching.

    Reads the cached file if present and ``refetch`` is False; otherwise
    issues a rate-limited GET via ``client`` (instantiating a default
    one if not supplied), writes the response to disk, and returns it.

    ``cache_dir`` defaults to the module-level ``RAW_FBREF_HTML`` (lazy
    resolution at call time so test monkey-patches take effect).
    """
    if cache_dir is None:
        cache_dir = RAW_FBREF_HTML
    path = cache_path_for(url, cache_dir=cache_dir)
    if path.exists() and not refetch:
        return FetchResult(
            url=url, html_path=path,
            payload=path.read_bytes(), from_cache=True,
        )
    if client is None:
        client = RateLimitedClient()
    payload = client.fetch(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return FetchResult(
        url=url, html_path=path, payload=payload, from_cache=False,
    )


# ─────────────────────────────────────────────────────────────────
#  Schedule parser  « one row per match on a season-schedule page »
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScheduledMatch:
    """One row parsed out of a fbref season-schedule page."""

    fbref_match_id: str        # 8-char hash that prefixes the match-report URL
    match_report_url: str
    match_date: date
    home_team: str
    away_team: str
    venue: str | None          # fbref's "Venue" column, often the stadium name
    referee: str | None


_MATCH_HASH_RE = re.compile(r"/en/matches/([0-9a-f]{8,})/")


def _parse_date_cell(text: str) -> date | None:
    """Parse fbref's date strings (typically ISO ``YYYY-MM-DD``)."""
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_schedule_html(html: bytes | str) -> list[ScheduledMatch]:
    """Extract every match row from a fbref season-schedule HTML page.

    fbref's schedule tables use stable ``data-stat`` attributes on each
    cell (``date``, ``home_team``, ``away_team``, ``venue``, ``referee``,
    plus a ``match_report`` cell whose link carries the fbref match id).
    Parsing keys off those attributes rather than column position so the
    parser survives column-order changes upstream.

    Rows without a match-report link (future fixtures, postponements) are
    silently skipped — they don't have a fbref_match_id yet.
    """
    soup = BeautifulSoup(html, "html.parser")
    out: list[ScheduledMatch] = []
    # fbref puts every season-schedule row under a single <tbody>, with
    # one <tr> per match.  Header / spacer rows have class="thead" — skip
    # them.
    for row in soup.find_all("tr"):
        classes = row.get("class") or []
        if "thead" in classes or "spacer" in classes:
            continue
        date_cell = row.find(attrs={"data-stat": "date"})
        if date_cell is None:
            continue
        match_date = _parse_date_cell(date_cell.get_text(strip=True))
        if match_date is None:
            continue
        report_cell = row.find(attrs={"data-stat": "match_report"})
        if report_cell is None:
            continue
        link = report_cell.find("a")
        if link is None or not link.get("href"):
            continue
        m = _MATCH_HASH_RE.search(link["href"])
        if m is None:
            continue
        home_cell = row.find(attrs={"data-stat": "home_team"})
        away_cell = row.find(attrs={"data-stat": "away_team"})
        venue_cell = row.find(attrs={"data-stat": "venue"})
        ref_cell = row.find(attrs={"data-stat": "referee"})
        out.append(
            ScheduledMatch(
                fbref_match_id=m.group(1),
                match_report_url=f"{FBREF_BASE}{link['href']}",
                match_date=match_date,
                home_team=(home_cell.get_text(strip=True) if home_cell else "") or "",
                away_team=(away_cell.get_text(strip=True) if away_cell else "") or "",
                venue=(venue_cell.get_text(strip=True) if venue_cell else None) or None,
                referee=(ref_cell.get_text(strip=True) if ref_cell else None) or None,
            )
        )
    return out


def fetch_and_parse_schedule(
    league_short_code: str,
    season: str,
    *,
    client: RateLimitedClient | None = None,
    refetch: bool = False,
) -> list[ScheduledMatch]:
    """End-to-end: build URL, fetch (cached), parse the schedule."""
    url = schedule_url(league_short_code, season)
    fetched = fetch_cached(url, client=client, refetch=refetch)
    return parse_schedule_html(fetched.payload)
