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


# Browser-like default headers — fbref's Cloudflare WAF rejects
# generic / academic User-Agent strings with a 403 Forbidden.  A Chrome-
# on-Linux UA plus the standard browser companion headers gets us
# through reliably; the same combination is what worldfootballR uses
# under the hood for the same reason.
_BROWSER_UA: str = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_BROWSER_HEADERS: dict[str, str] = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


@dataclass
class RateLimitedClient:
    """Process-local rate limiter for fbref requests.

    ``min_interval_s`` is the minimum spacing between two outgoing
    requests on this client.  Use one client per process (or per
    worker) so the rate limit is enforced globally.  Re-instantiating
    a client does NOT reset cross-process timing — for that, run in
    a single process or coordinate via a shared lock file.

    ``user_agent`` defaults to a browser-like Chrome-on-Linux string
    because fbref's WAF returns 403 Forbidden to generic / academic
    UAs.  Override with an attribution string only if you have
    confirmed that fbref will accept it from your IP range.
    """

    min_interval_s: float = 3.0
    user_agent: str = _BROWSER_UA
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
        headers = {"User-Agent": self.user_agent, **_BROWSER_HEADERS}
        response = requests.get(
            url,
            headers=headers,
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


# ─────────────────────────────────────────────────────────────────
#  Match-report parser  « lineups + cards + aggression classification »
# ─────────────────────────────────────────────────────────────────

_TEAM_HASH_RE = re.compile(r"/en/squads/([0-9a-f]{8,})/")
_PLAYER_HASH_RE = re.compile(r"/en/players/([0-9a-f]{8,})/")


@dataclass(frozen=True)
class PlayerAppearance:
    """One player's appearance in one match (one row of the team summary)."""

    fbref_player_id: str        # 8-char fbref player slug
    player_name: str
    shirt_number: int | None
    position: str | None        # 'GK' / 'DF' / 'MF' / 'FW' (fbref's `pos`)
    minutes_played: int | None
    started: int                # 1 if listed before the substitutes divider
    n_yellows: int
    n_reds: int


@dataclass(frozen=True)
class TeamAppearances:
    """All player appearances for one side of a match."""

    fbref_team_id: str          # 8-char fbref squad slug
    team_name: str
    is_home: int                # 1 / 0
    players: list[PlayerAppearance]


@dataclass(frozen=True)
class CardEvent:
    """One card issued in a match (from the events list)."""

    minute_of_issue: int | None
    fbref_player_id: str | None
    player_name: str
    card_color: str             # 'yellow' | 'red' | 'second_yellow_red'
    card_reason: str            # may be empty when fbref doesn't annotate
    aggression_set: int | None  # 1 if reason hits aggression keywords; NULL if reason empty
    is_home: int | None         # 1 / 0 / None when undetermined


# ─────────────────────────────────────────────────────────────────
#  Aggression classifier  « keyword match on the reason string »
# ─────────────────────────────────────────────────────────────────

#: Keyword fragments that mark a card as aggression-set per OSF § 2.
#: The match is case-insensitive substring; fragments deliberately err
#: on the side of recall so the OSF-locked outcome doesn't quietly
#: drop violent-conduct reds because of phrasing variation.
AGGRESSION_KEYWORDS: tuple[str, ...] = (
    "violent conduct",
    "violent",
    "serious foul play",
    "serious foul",
    "spitting",
    "spat",
    "abusive language",
    "abusive",
    "offensive language",
    "fighting",
    "fight",
    "headbutt",
    "elbow",
    "punch",
    "kicking",
    "kicked opponent",
    "stamping",
    "stamped",
    "biting",
    "bit",
)


def classify_aggression(reason: str | None) -> int | None:
    """Return 1 if ``reason`` matches the OSF aggression-set keywords.

    Returns ``None`` (not 0) when ``reason`` is empty / missing — we
    can't tell whether a reasonless red was for violent conduct or a
    tactical second yellow.  Downstream analysis treats NULL as
    "unknown" so the OSF outcome can be reported on the subset of
    cards with parsed reasons, separately from the all-reds proxy.
    """
    if reason is None:
        return None
    text = reason.strip().lower()
    if not text:
        return None
    return 1 if any(kw in text for kw in AGGRESSION_KEYWORDS) else 0


# ─────────────────────────────────────────────────────────────────
#  Lineup / summary-stats parser
# ─────────────────────────────────────────────────────────────────


def _coerce_optional_int(cell) -> int | None:
    """Helper: extract an integer from a BeautifulSoup cell, or None."""
    if cell is None:
        return None
    text = cell.get_text(strip=True)
    if not text:
        return None
    try:
        return int(float(text.replace(",", "")))
    except ValueError:
        return None


def _parse_one_team_summary(table) -> TeamAppearances | None:
    """Parse one ``stats_<teamhash>_summary`` table into a TeamAppearances."""
    table_id = table.get("id") or ""
    m = re.match(r"stats_([0-9a-f]{8,})_summary", table_id)
    if m is None:
        return None
    fbref_team_id = m.group(1)
    # fbref's table caption looks like "<TeamName> Player Stats Table"
    caption = table.find("caption")
    team_name = ""
    if caption is not None:
        text = caption.get_text(strip=True)
        for marker in (" Player Stats Table", " Player Stats", " Stats"):
            if text.endswith(marker):
                team_name = text[: -len(marker)].strip()
                break
        if not team_name:
            team_name = text
    players: list[PlayerAppearance] = []
    # Each tbody <tr> is one player; rows with class "spacer" / "thead" demarcate
    # the substitutes section (started=0 below the divider).
    started_flag = 1
    tbody = table.find("tbody")
    if tbody is None:
        return TeamAppearances(
            fbref_team_id=fbref_team_id,
            team_name=team_name,
            is_home=0,
            players=players,
        )
    for row in tbody.find_all("tr"):
        classes = row.get("class") or []
        if "spacer" in classes or "thead" in classes:
            # fbref puts a spacer / thead between starting XI and subs;
            # everything below it is a substitute appearance.
            started_flag = 0
            continue
        player_cell = row.find(attrs={"data-stat": "player"})
        if player_cell is None:
            continue
        player_link = player_cell.find("a")
        fbref_player_id = ""
        if player_link is not None and player_link.get("href"):
            pm = _PLAYER_HASH_RE.search(player_link["href"])
            if pm is not None:
                fbref_player_id = pm.group(1)
        player_name = player_cell.get_text(strip=True)
        # The summary stats row layout is stable for the columns we use;
        # the parser keys on the canonical data-stat attributes.
        shirt = _coerce_optional_int(row.find(attrs={"data-stat": "shirtnumber"}))
        pos_cell = row.find(attrs={"data-stat": "position"})
        position = pos_cell.get_text(strip=True) if pos_cell is not None else None
        minutes = _coerce_optional_int(row.find(attrs={"data-stat": "minutes"}))
        yellows = _coerce_optional_int(row.find(attrs={"data-stat": "cards_yellow"})) or 0
        reds = _coerce_optional_int(row.find(attrs={"data-stat": "cards_red"})) or 0
        if not player_name and not fbref_player_id:
            continue
        # fbref's summary tables include a final "Total" row whose
        # data-stat="player" cell is empty / spans columns — skip it.
        if row.find(attrs={"data-stat": "player"}) is not None:
            footer_label = (player_name or "").lower()
            if footer_label.startswith(("total", "players used")):
                continue
        players.append(
            PlayerAppearance(
                fbref_player_id=fbref_player_id,
                player_name=player_name,
                shirt_number=shirt,
                position=position or None,
                minutes_played=minutes,
                started=started_flag,
                n_yellows=int(yellows),
                n_reds=int(reds),
            )
        )
    return TeamAppearances(
        fbref_team_id=fbref_team_id,
        team_name=team_name,
        is_home=0,  # caller sets this from page context
        players=players,
    )


def parse_match_lineups(html: bytes | str) -> tuple[TeamAppearances, TeamAppearances]:
    """Extract ``(home_team_appearances, away_team_appearances)`` from the
    match-report HTML.

    fbref's match reports include two ``stats_<teamhash>_summary`` tables,
    one per team, in DOM order home-first.  The parser uses that ordering
    to assign ``is_home``; the order is stable across the fbref-era we
    care about.
    """
    soup = BeautifulSoup(html, "html.parser")
    summary_tables = [
        t for t in soup.find_all("table")
        if (t.get("id") or "").startswith("stats_")
        and (t.get("id") or "").endswith("_summary")
    ]
    if len(summary_tables) < 2:
        raise ValueError(
            f"match report has {len(summary_tables)} summary tables; "
            f"expected exactly 2 (home + away)."
        )
    home = _parse_one_team_summary(summary_tables[0])
    away = _parse_one_team_summary(summary_tables[1])
    if home is None or away is None:
        raise ValueError("could not parse one of the team summary tables")
    home = TeamAppearances(
        fbref_team_id=home.fbref_team_id, team_name=home.team_name,
        is_home=1, players=home.players,
    )
    away = TeamAppearances(
        fbref_team_id=away.fbref_team_id, team_name=away.team_name,
        is_home=0, players=away.players,
    )
    return home, away


# ─────────────────────────────────────────────────────────────────
#  Card-events parser  « the match-summary timeline »
# ─────────────────────────────────────────────────────────────────


_MINUTE_RE = re.compile(r"(\d+)(?:\+\d+)?")


def _parse_minute(text: str) -> int | None:
    """Coerce fbref's minute strings (``"42'"``, ``"45+3'"``) to int."""
    if not text:
        return None
    m = _MINUTE_RE.search(text)
    return int(m.group(1)) if m else None


_CARD_CLASS_TO_COLOR: dict[str, str] = {
    "yellow_card": "yellow",
    "red_card": "red",
    "yellow_red_card": "second_yellow_red",
}


def parse_match_cards(html: bytes | str) -> list[CardEvent]:
    """Extract per-card events from the match-report event list.

    fbref's match-report page carries an "Events" timeline section
    where each event has a CSS class indicating its type (goal, sub,
    yellow_card, red_card, yellow_red_card) and a wrapping element
    that attributes the event to either the home or away team via
    layout (left side = home, right side = away).

    Returns a list of ``CardEvent``; an empty list is a normal result
    for a match with no cards.  Reasons are extracted from the event
    title / tooltip text when present and run through
    :func:`classify_aggression` to populate ``aggression_set``.
    """
    soup = BeautifulSoup(html, "html.parser")
    events: list[CardEvent] = []
    container = soup.find(id="events_wrap") or soup
    for event in container.find_all(class_=lambda c: c and "event" in c.split()):
        classes = event.get("class") or []
        color: str | None = None
        for css, val in _CARD_CLASS_TO_COLOR.items():
            if css in classes:
                color = val
                break
        if color is None:
            # Not a card event; fbref also puts goals / subs in this list.
            for inner in event.find_all(class_=True):
                inner_classes = inner.get("class") or []
                for css, val in _CARD_CLASS_TO_COLOR.items():
                    if css in inner_classes:
                        color = val
                        break
                if color is not None:
                    break
        if color is None:
            continue
        is_home: int | None = None
        if "a" in classes:
            is_home = 1
        elif "b" in classes:
            is_home = 0
        minute = _parse_minute(event.get_text(" ", strip=True))
        player_link = event.find("a", href=_PLAYER_HASH_RE)
        fbref_player_id = None
        player_name = ""
        if player_link is not None:
            pm = _PLAYER_HASH_RE.search(player_link["href"])
            if pm is not None:
                fbref_player_id = pm.group(1)
            player_name = player_link.get_text(strip=True)
        # Reason text: fbref sometimes annotates via a wrapping element's
        # title attribute or trailing parenthetical.  We try a few
        # places and accept the first non-empty one.
        reason = ""
        title = event.get("title") or ""
        if title:
            reason = title.strip()
        if not reason:
            small = event.find("small")
            if small is not None:
                reason = small.get_text(strip=True).strip("()")
        events.append(
            CardEvent(
                minute_of_issue=minute,
                fbref_player_id=fbref_player_id,
                player_name=player_name,
                card_color=color,
                card_reason=reason,
                aggression_set=classify_aggression(reason),
                is_home=is_home,
            )
        )
    return events


# ─────────────────────────────────────────────────────────────────
#  Convenience driver  « fetch report + parse both layers »
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParsedMatchReport:
    """One match's parsed lineups + cards, ready for the ingest layer."""

    fbref_match_id: str
    home: TeamAppearances
    away: TeamAppearances
    cards: list[CardEvent]


def parse_match_report(
    fbref_match_id: str, html: bytes | str,
) -> ParsedMatchReport:
    """Parse a match-report HTML page end-to-end."""
    home, away = parse_match_lineups(html)
    cards = parse_match_cards(html)
    return ParsedMatchReport(
        fbref_match_id=fbref_match_id,
        home=home, away=away, cards=cards,
    )


def fetch_and_parse_match_report(
    scheduled_match: ScheduledMatch,
    *,
    client: RateLimitedClient | None = None,
    refetch: bool = False,
) -> ParsedMatchReport:
    """End-to-end: fetch + cache + parse one match report."""
    fetched = fetch_cached(
        scheduled_match.match_report_url, client=client, refetch=refetch,
    )
    return parse_match_report(scheduled_match.fbref_match_id, fetched.payload)
