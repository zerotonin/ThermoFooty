"""fbref match-report parser tests — offline, fixture HTML.

Phase 3b coverage: lineup table parsing, card-event timeline parsing,
aggression-set classification.  All tests drive the parsers from a
committed HTML fixture; CI never touches the network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from thermofooty.sources.fbref import (
    AGGRESSION_KEYWORDS,
    CardEvent,
    TeamAppearances,
    classify_aggression,
    parse_match_cards,
    parse_match_lineups,
    parse_match_report,
)

FIXTURE = (
    Path(__file__).parent / "fixtures" / "fbref"
    / "match_report_aa1de559_excerpt.html"
)


@pytest.fixture(scope="module")
def html() -> bytes:
    return FIXTURE.read_bytes()


# ─────────────────────────────────────────────────────────────────
#  Aggression classifier
# ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reason",
    [
        "Violent conduct",
        "violent conduct",
        "Serious foul play",
        "Spitting at an opponent",
        "Abusive language towards the referee",
        "Fighting an opponent",
        "Headbutt on opponent",
        "Elbowed an opponent",
    ],
)
def test_classify_aggression_marks_osf_set_as_one(reason):
    assert classify_aggression(reason) == 1


@pytest.mark.parametrize(
    "reason",
    ["Foul", "Dissent", "Time-wasting", "Handball", "Off the ball"],
)
def test_classify_aggression_marks_non_osf_as_zero(reason):
    assert classify_aggression(reason) == 0


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_classify_aggression_returns_none_for_missing_reason(reason):
    """Missing reason text must not be silently coerced to 0 — downstream
    analysis treats NULL as 'unknown' and reports separately.
    """
    assert classify_aggression(reason) is None


def test_aggression_keywords_includes_canonical_osf_phrases():
    """Sanity: a few specific phrases the OSF pre-reg names must be in
    the keyword list.  Renames or accidental deletions are caught here.
    """
    flat = " | ".join(AGGRESSION_KEYWORDS)
    for canonical in ("violent conduct", "serious foul play", "spitting"):
        assert canonical in flat, f"missing canonical OSF keyword {canonical!r}"


# ─────────────────────────────────────────────────────────────────
#  Lineup parser
# ─────────────────────────────────────────────────────────────────


def test_parse_match_lineups_returns_home_then_away(html):
    home, away = parse_match_lineups(html)
    assert isinstance(home, TeamAppearances)
    assert isinstance(away, TeamAppearances)
    assert home.is_home == 1
    assert away.is_home == 0


def test_parse_match_lineups_extracts_team_id_and_name(html):
    home, away = parse_match_lineups(html)
    assert home.fbref_team_id == "b8fd03ef"
    assert home.team_name == "Manchester City"
    assert away.fbref_team_id == "943e8050"
    assert away.team_name == "Burnley"


def test_parse_match_lineups_extracts_player_appearances(html):
    home, _ = parse_match_lineups(html)
    # Three starters + one sub on the home side fixture
    assert len(home.players) == 4
    by_name = {p.player_name: p for p in home.players}
    assert "Ederson" in by_name
    assert by_name["Ederson"].fbref_player_id == "8effd185"
    assert by_name["Ederson"].position == "GK"
    assert by_name["Ederson"].minutes_played == 90


def test_parse_match_lineups_marks_subs_after_spacer(html):
    """Players listed below the <tr class='spacer'> divider must have
    started=0; those above it started=1.
    """
    home, _ = parse_match_lineups(html)
    by_name = {p.player_name: p for p in home.players}
    assert by_name["Ederson"].started == 1
    # Álvarez is below the spacer in the fixture
    sub_name = next(n for n in by_name if "Álvarez" in n)
    assert by_name[sub_name].started == 0


def test_parse_match_lineups_carries_card_counts(html):
    """The per-row cards_yellow / cards_red cells must be parsed so the
    reconciliation pass (Phase 3d) can sum them per side.
    """
    home, away = parse_match_lineups(html)
    rodri = next(p for p in home.players if p.player_name == "Rodri")
    assert rodri.n_yellows == 1
    assert rodri.n_reds == 0
    foster = next(p for p in away.players if p.player_name == "Lyle Foster")
    assert foster.n_reds == 1
    assert foster.n_yellows == 0


def test_parse_match_lineups_raises_when_fewer_than_two_teams():
    """A malformed report (only one team table) must raise — silent
    skipping would leave one side missing from the analysis panel.
    """
    with pytest.raises(ValueError, match="summary tables"):
        parse_match_lineups("<html><body>no tables</body></html>")


# ─────────────────────────────────────────────────────────────────
#  Card-events parser
# ─────────────────────────────────────────────────────────────────


def test_parse_match_cards_extracts_all_card_events(html):
    cards = parse_match_cards(html)
    assert len(cards) == 3
    assert all(isinstance(c, CardEvent) for c in cards)
    colors = sorted(c.card_color for c in cards)
    assert colors == ["red", "yellow", "yellow"]


def test_parse_match_cards_assigns_home_away_via_class(html):
    """Events with class 'a' belong to the home side; class 'b' to away."""
    cards = parse_match_cards(html)
    home_cards = [c for c in cards if c.is_home == 1]
    away_cards = [c for c in cards if c.is_home == 0]
    # Fixture: 1 home yellow (Rodri); 1 away red (Foster); 1 away yellow (Brownhill)
    assert len(home_cards) == 1
    assert len(away_cards) == 2


def test_parse_match_cards_attaches_player_id_and_minute(html):
    cards = parse_match_cards(html)
    foster_red = next(c for c in cards if c.card_color == "red")
    assert foster_red.fbref_player_id == "c7195ce6"
    assert foster_red.player_name == "Lyle Foster"
    assert foster_red.minute_of_issue == 72


def test_parse_match_cards_classifies_violent_conduct_as_aggression(html):
    cards = parse_match_cards(html)
    foster_red = next(c for c in cards if c.card_color == "red")
    assert foster_red.card_reason == "Violent conduct"
    assert foster_red.aggression_set == 1


def test_parse_match_cards_does_not_flag_tactical_fouls(html):
    cards = parse_match_cards(html)
    rodri_yellow = next(c for c in cards if c.player_name == "Rodri")
    assert rodri_yellow.card_reason == "Foul"
    assert rodri_yellow.aggression_set == 0


# ─────────────────────────────────────────────────────────────────
#  parse_match_report  « end-to-end convenience »
# ─────────────────────────────────────────────────────────────────


def test_parse_match_report_bundles_lineups_and_cards(html):
    parsed = parse_match_report("aa1de559", html)
    assert parsed.fbref_match_id == "aa1de559"
    assert parsed.home.team_name == "Manchester City"
    assert parsed.away.team_name == "Burnley"
    assert len(parsed.cards) == 3
    # The OSF-locked aggression-set flag survives the round trip
    aggr = [c for c in parsed.cards if c.aggression_set == 1]
    assert len(aggr) == 1
    assert aggr[0].player_name == "Lyle Foster"


def test_lineup_card_counts_match_event_card_counts(html):
    """Sanity invariant: summing n_yellows + n_reds across all players
    must equal the count of card events parsed from the timeline.
    This protects against accidental double-counting in either parser.
    """
    home, away = parse_match_lineups(html)
    cards = parse_match_cards(html)
    total_player_yellows = sum(
        p.n_yellows for team in (home, away) for p in team.players
    )
    total_player_reds = sum(
        p.n_reds for team in (home, away) for p in team.players
    )
    total_event_yellows = sum(1 for c in cards if c.card_color == "yellow")
    total_event_reds = sum(
        1 for c in cards if c.card_color in ("red", "second_yellow_red")
    )
    assert total_player_yellows == total_event_yellows
    assert total_player_reds == total_event_reds
