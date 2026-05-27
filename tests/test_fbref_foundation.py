"""fbref scraper foundation tests — offline, fixture HTML, no network.

Covers Phase 3a: URL builder, on-disk cache layout, rate-limited
client timing invariants, schedule HTML parser.  The match-report
parser + lineup/card extraction land in Phase 3b with their own
test file + fixtures.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pytest

from thermofooty.sources import fbref
from thermofooty.sources.fbref import (
    COMP_IDS,
    COMP_SLUGS,
    FBREF_BASE,
    FetchResult,
    RateLimitedClient,
    ScheduledMatch,
    cache_path_for,
    fetch_cached,
    parse_schedule_html,
    schedule_url,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "fbref"
SCHEDULE_FIXTURE = FIXTURE_DIR / "schedule_epl_2023_24_excerpt.html"


# ─────────────────────────────────────────────────────────────────
#  URL builder
# ─────────────────────────────────────────────────────────────────


def test_schedule_url_widens_two_digit_season_suffix():
    """Project uses '2023-24'; fbref uses '2023-2024'."""
    url = schedule_url("EN_PREM", "2023-24")
    assert "2023-2024" in url
    assert "/comps/9/" in url
    assert url.endswith("2023-2024-Premier-League-Scores-and-Fixtures")


def test_schedule_url_handles_1999_2000_century_boundary():
    """Edge case: 1999-2000 must widen the end suffix to 2000, not 1900."""
    url = schedule_url("EN_PREM", "1999-2000")
    assert "1999-2000" in url
    assert "1899" not in url


def test_schedule_url_unknown_short_code_raises():
    with pytest.raises(KeyError, match="unknown"):
        schedule_url("XX_NOPE", "2023-24")


def test_comp_metadata_covers_phase2c_panel():
    """The fbref scraper must support every league we've already ingested
    in Phase 2c — EPL + Championship + League One at minimum.
    """
    for code in ("EN_PREM", "EN_CHAMP", "EN_L1"):
        assert code in COMP_IDS
        assert code in COMP_SLUGS


# ─────────────────────────────────────────────────────────────────
#  Cache key path
# ─────────────────────────────────────────────────────────────────


def test_cache_path_for_includes_sha_and_readable_tail(tmp_path: Path):
    url = "https://fbref.com/en/comps/9/2023-2024/schedule/foo"
    path = cache_path_for(url, cache_dir=tmp_path)
    assert path.parent == tmp_path
    assert path.suffix == ".html"
    assert "foo" in path.name
    # 12-char SHA-1 prefix
    sha = path.name.split("_", 1)[0]
    assert len(sha) == 12
    assert all(c in "0123456789abcdef" for c in sha)


def test_cache_path_collisions_are_url_unique(tmp_path: Path):
    """Two URLs that differ only after the last path component must hash
    to different cache paths (the SHA-1 covers the whole URL, not just
    the tail).
    """
    a = cache_path_for("https://fbref.com/en/comps/9/2023-2024/schedule/x", cache_dir=tmp_path)
    b = cache_path_for("https://fbref.com/en/comps/9/2022-2023/schedule/x", cache_dir=tmp_path)
    assert a != b


# ─────────────────────────────────────────────────────────────────
#  RateLimitedClient timing  « no network — monkeypatch sleep + time »
# ─────────────────────────────────────────────────────────────────


def test_rate_limited_client_enforces_min_interval(monkeypatch):
    """Two back-to-back fetch() calls on the same client must sleep at
    least min_interval_s between the first response and the second
    request.
    """
    elapsed_sleeps: list[float] = []
    fake_now = [100.0]

    def fake_sleep(s: float) -> None:
        elapsed_sleeps.append(s)
        fake_now[0] += s

    def fake_monotonic() -> float:
        return fake_now[0]

    monkeypatch.setattr(time, "sleep", fake_sleep)
    monkeypatch.setattr(time, "monotonic", fake_monotonic)

    # Stub the requests call to advance time by ~0.1 s per request
    def fake_get(url, headers=None, timeout=None):
        class _R:
            content = b"<html>ok</html>"
            def raise_for_status(self_inner): return None
        fake_now[0] += 0.1
        return _R()

    import requests
    monkeypatch.setattr(requests, "get", fake_get)

    client = RateLimitedClient(min_interval_s=3.0)
    client.fetch("https://example.test/a")
    client.fetch("https://example.test/b")

    # First request: no sleep needed (last_request_at was 0).
    # Second request: needed to sleep ~(3 - 0.1) = 2.9 s minimum.
    assert any(s >= 2.5 for s in elapsed_sleeps)


# ─────────────────────────────────────────────────────────────────
#  fetch_cached  « hits disk first »
# ─────────────────────────────────────────────────────────────────


def test_fetch_cached_returns_from_disk_when_present(tmp_path: Path):
    url = "https://fbref.com/en/test/page"
    cache_path = cache_path_for(url, cache_dir=tmp_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"<html>cached payload</html>")

    result = fetch_cached(url, cache_dir=tmp_path)
    assert isinstance(result, FetchResult)
    assert result.from_cache is True
    assert result.payload == b"<html>cached payload</html>"


def test_fetch_cached_refetch_flag_bypasses_cache(monkeypatch, tmp_path: Path):
    url = "https://fbref.com/en/test/refetch"
    cache_path = cache_path_for(url, cache_dir=tmp_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"<html>stale</html>")

    fetched_urls: list[str] = []

    class _StubClient:
        def fetch(self, u):
            fetched_urls.append(u)
            return b"<html>fresh</html>"

    result = fetch_cached(
        url, client=_StubClient(), cache_dir=tmp_path, refetch=True,
    )
    assert result.from_cache is False
    assert result.payload == b"<html>fresh</html>"
    assert fetched_urls == [url]
    # The cache file is now overwritten with the fresh bytes
    assert cache_path.read_bytes() == b"<html>fresh</html>"


# ─────────────────────────────────────────────────────────────────
#  Schedule HTML parser
# ─────────────────────────────────────────────────────────────────


def test_fixture_file_exists():
    """The committed fixture is the parser's only test ground truth."""
    assert SCHEDULE_FIXTURE.exists(), (
        f"missing fbref schedule fixture at {SCHEDULE_FIXTURE}"
    )


def test_parse_schedule_html_extracts_completed_matches():
    html = SCHEDULE_FIXTURE.read_bytes()
    matches = parse_schedule_html(html)
    # 3 completed matches + 1 future fixture + 1 postponed; the parser
    # should only return the 3 completed ones (those with match_report links).
    assert len(matches) == 3
    assert all(isinstance(m, ScheduledMatch) for m in matches)


def test_parse_schedule_html_carries_match_ids_and_urls():
    matches = parse_schedule_html(SCHEDULE_FIXTURE.read_bytes())
    by_date = {m.match_date: m for m in matches}
    aug_11 = by_date[date(2023, 8, 11)]
    assert aug_11.fbref_match_id == "aa1de559"
    assert aug_11.match_report_url.startswith(FBREF_BASE)
    assert "Manchester-City-Burnley" in aug_11.match_report_url
    assert aug_11.home_team == "Manchester City"
    assert aug_11.away_team == "Burnley"
    assert aug_11.venue == "Etihad Stadium"
    assert aug_11.referee == "Craig Pawson"


def test_parse_schedule_html_skips_future_and_postponed_rows():
    """Rows without a match_report link (future, postponed) must be
    silently dropped — they don't have a fbref_match_id yet.
    """
    matches = parse_schedule_html(SCHEDULE_FIXTURE.read_bytes())
    # Two excluded rows both have date 2024-05-19 OR 2024-02-03.  The
    # 2024-05-19 row that DOES survive is Liverpool vs Wolves; the
    # Arsenal vs Everton row on the same date must be absent.
    home_teams = [m.home_team for m in matches]
    assert "Arsenal" not in home_teams
    # 2024-02-03 postponed Chelsea row must be absent entirely
    assert date(2024, 2, 3) not in {m.match_date for m in matches}


def test_parse_schedule_html_handles_bytes_and_str():
    """The parser must accept either raw bytes or a decoded string."""
    raw = SCHEDULE_FIXTURE.read_bytes()
    text = SCHEDULE_FIXTURE.read_text(encoding="utf-8")
    assert len(parse_schedule_html(raw)) == len(parse_schedule_html(text))


# ─────────────────────────────────────────────────────────────────
#  fetch_and_parse_schedule  « end-to-end with fixture-backed cache »
# ─────────────────────────────────────────────────────────────────


def test_fetch_and_parse_schedule_uses_cache_when_present(tmp_path, monkeypatch):
    """If we pre-populate the cache with the fixture HTML, the orchestration
    helper should never call the network.  Asserts via monkey-patched
    RateLimitedClient that raises if fetch() is invoked.
    """
    url = schedule_url("EN_PREM", "2023-24")
    cache_path = cache_path_for(url, cache_dir=tmp_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(SCHEDULE_FIXTURE.read_bytes())

    monkeypatch.setattr(fbref, "RAW_FBREF_HTML", tmp_path)

    class _ExplodingClient:
        def fetch(self, _u):  # pragma: no cover - defensive
            raise AssertionError("network must not be hit when cache is warm")

    matches = fbref.fetch_and_parse_schedule(
        "EN_PREM", "2023-24", client=_ExplodingClient(),
    )
    assert len(matches) == 3
