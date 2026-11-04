from datetime import UTC, datetime, timedelta

from services import espn_props, prop_markets

NOW = datetime.now(UTC)
KICKOFF = (NOW + timedelta(hours=4)).isoformat()
HARDEN = "http://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/2027/athletes/3992"


def item(kind, line, decimal, ref=HARDEN):
    return {"athlete": {"$ref": ref}, "type": {"name": kind}, "current": {"target": {"value": line}},
            "odds": {"decimal": {"value": str(decimal)}}}


def test_main_lines_pair_over_first_and_milestones_become_over_alternates():
    items = [item("Total Points", 17.5, 1.84), item("Total Points", 17.5, 1.92),
             item("Points Milestones", 15.0, 1.41), item("Points Milestones", 18.0, 1.84), item("First Basket", 1, 9.0)]
    pairs, alternates = espn_props.shape("nba", items, {HARDEN: "James Harden"}, NOW.isoformat(), KICKOFF)
    assert pairs == [{**pairs[0], "player": "jamesharden", "market": "player_points", "line": 17.5,
                      "over_decimal": 1.84, "under_decimal": 1.92, "bookmaker": "DraftKings"}]
    assert [(a["market"], a["line"], a["side"], a["price"]) for a in alternates] == [
        ("player_points_alternate", 14.5, "over", 1.41), ("player_points_alternate", 17.5, "over", 1.84)]


def test_alternate_ladders_are_used_only_when_anchored_and_in_order():
    hart = "hartenstein"
    stale = [item("Total Rebounds", 9.5, 1.714, hart), item("Total Rebounds", 9.5, 2.06, hart),
             item("Rebounds Milestones", 4.0, 2.53, hart)]                       # ESPN's stale "4+ +153"
    pairs, alternates = espn_props.shape("nba", stale, {hart: "Isaiah Hartenstein"}, NOW.isoformat(), KICKOFF)
    assert len(pairs) == 1 and alternates == []
    disorder = [item("Total Points", 17.5, 1.84), item("Total Points", 17.5, 1.92), item("Points Milestones", 18.0, 1.84),
                item("Points Milestones", 15.0, 2.2)]                             # 15+ priced longer than 18+
    assert espn_props.shape("nba", disorder, {HARDEN: "James Harden"}, NOW.isoformat(), KICKOFF)[1] == []


def test_soccer_ladders_need_two_ordered_rungs_and_anytime_scorer_stands_alone():
    kane, saka = "kane", "saka"
    items = [item("Shots on Target Milestones", 1.0, 1.25, kane), item("Shots on Target Milestones", 2.0, 2.4, kane),
             item("Shots Milestones", 3.0, 2.1, saka),                                  # one rung, nothing to check it against
             {"athlete": {"$ref": kane}, "type": {"name": "Anytime Goalscorer"}, "odds": {"decimal": {"value": "1.8"}}},
             item("Shots on Target Milestones", 1.0, 1.5, saka)]                       # unpriced lines never reach here
    names = {kane: "Harry Kane", saka: "Bukayo Saka"}
    _pairs, alternates = espn_props.shape("soccer", items, names, NOW.isoformat(), KICKOFF)
    got = sorted((a["player"], a["market"], a["line"]) for a in alternates)
    assert got == [("harrykane", "player_goals_alternate", 0.5), ("harrykane", "player_shots_on_target_alternate", 0.5),
                   ("harrykane", "player_shots_on_target_alternate", 1.5)]
    unpriced = [{"athlete": {"$ref": kane}, "type": {"name": "Shots Milestones"}, "current": {"target": {"value": 2.0}}}]
    assert espn_props.shape("soccer", unpriced, names, NOW.isoformat(), KICKOFF)[1] == []


def test_a_pair_the_milestone_contradicts_is_flipped():
    items = [item("Total Points", 17.5, 1.92), item("Total Points", 17.5, 1.84), item("Points Milestones", 18.0, 1.84)]
    pairs, _ = espn_props.shape("nba", items, {HARDEN: "James Harden"}, NOW.isoformat(), KICKOFF)
    assert (pairs[0]["over_decimal"], pairs[0]["under_decimal"]) == (1.84, 1.92)


def test_whole_lines_unknown_players_and_lone_prices_are_dropped():
    items = [item("Total Points", 18.0, 1.9), item("Total Points", 18.0, 1.9),        # whole line can push
             item("Total Rebounds", 6.5, 1.9),                                         # one side only
             item("Total Points", 20.5, 1.9, ref="other"), item("Total Points", 20.5, 1.9, ref="other")]
    pairs, _ = espn_props.shape("nba", items, {HARDEN: "James Harden"}, NOW.isoformat(), KICKOFF)
    assert pairs == []


def test_snapshot_uses_espn_by_default_and_spends_no_credits(monkeypatch):
    monkeypatch.setenv("PROP_ODDS_SOURCE", "espn")
    monkeypatch.setattr(prop_markets, "_fetch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Odds API called")))
    monkeypatch.setattr(prop_markets, "_preserve_quotes", lambda *a, **k: None)
    pair = {"player": "jamesharden", "market": "player_points", "line": 17.5, "over_decimal": 1.84, "under_decimal": 1.92,
            "bookmaker": "DraftKings", "bookmaker_key": "draftkings", "updated_at": NOW.isoformat(), "fetched_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(minutes=5)).isoformat()}
    other = {**pair, "market": "player_rebounds"}
    monkeypatch.setattr(espn_props, "quotes", lambda *a, **k: {"status": "available", "quotes": [pair, other], "alternates": [],
                                                                "event_id": "espn-1", "fetched_at": NOW.isoformat()})
    feed = prop_markets.snapshot("nba", "Cleveland Cavaliers", "Oklahoma City Thunder", KICKOFF, {"player_points"})
    assert feed["status"] == "available" and feed["quotes"] == [pair] and feed["source"] == "ESPN (DraftKings)"


def test_a_sport_espn_cannot_price_falls_back_to_the_capped_odds_api(monkeypatch):
    monkeypatch.setenv("PROP_ODDS_SOURCE", "espn")
    monkeypatch.setenv("ODDS_API_KEY", "")  # no key here, so the fallback reports not_configured rather than fetching
    monkeypatch.setattr(espn_props, "quotes", lambda *a, **k: {"status": "no_quotes", "quotes": [], "alternates": []})
    feed = prop_markets.snapshot("nfl", "Seattle Seahawks", "Chicago Bears", KICKOFF, {"player_pass_yds"})
    assert feed["status"] == "not_configured"
