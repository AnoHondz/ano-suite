"""The 2026-11-04 audit fixes: value policy, decision engine, immutable pick records, closing prices, scorecard."""
import json
from datetime import UTC, date, datetime

from services import chrono_validation as cv
from services import pick_tracker as pt
from services import value_policy as vp
from services.decision_engine import DecisionEngine


def test_no_vig_removes_the_margin_and_value_needs_four_percent():
    assert abs(vp.no_vig(1.909, 1.909) - 0.5) < 1e-9          # -110 / -110 is a coin flip, not 52.4%
    assert vp.no_vig(2.0) < 0.5                                # one side priced: a typical margin is taken off
    assert vp.is_value({"chance": 0.55, "price": 1.909})       # +5.0%
    assert not vp.is_value({"chance": 0.54, "price": 1.909})   # +3.1%: below the bar
    assert vp.cautious(0.8, 10) < vp.cautious(0.8, 200) < 0.8  # thin evidence counts for less
    assert vp.tag([{"chance": .6, "price": 1.8}, {"chance": .5, "price": 1.9}]) == vp.TAG_BEST


def test_half_the_edge_counts_and_every_source_must_clear_the_fair_price():
    from services import tiered_parlays as tp
    # the 2026-11-04 Towns leg: 61% from a 7-of-15 record plus the model, against a fair 49% at 1.92
    leg = tp.priced({"chance": 0.6113, "price": 1.92}, 0.4894, 60, {"record": 0.47, "model": 0.75})
    assert abs(leg["chance"] - (0.4894 + 0.5 * (0.6113 - 0.4894))) < 1e-4 and leg["raw_chance"] == 0.6113
    assert vp.split(leg) and not vp.is_value(leg)            # the record sits below the fair price: not value
    assert "disagree" in vp.value_sentence(leg)
    agreed = tp.priced({"chance": 0.66, "price": 1.92}, 0.49, 60, {"record": 0.6, "model": 0.72})
    assert vp.is_value(agreed) and vp.value_sentence(agreed).startswith("Value: +")
    short = tp.priced({"chance": 0.76, "price": 1.30}, 0.7361, 60)   # the Botheim fouls leg
    assert vp.value_sentence(short).startswith("Negative value")


def test_choose_on_the_selection_period_and_report_the_untouched_test_once():
    # "lucky" is best only in the selection period; "steady" is best overall. The test score reported is the chosen one's.
    def replay(name):
        return [(t, (0.10 if 60 <= t < 80 else 0.30) if name == "lucky" else 0.20) for t in range(100)]
    result = cv.choose_then_test({"lucky": "lucky", "steady": "steady"}, replay)
    assert result["chosen"] == "lucky" and result["result"][0] == 0.30   # selection luck does not carry into the test
    assert result["test"]["steady"][0] == 0.20


def test_decision_engine_skips_without_probabilities_and_never_considers_negative_value():
    engine = DecisionEngine()
    blank = engine.build_decision({"home_name": "A", "away_name": "B", "confidence": "High", "odds": {"home": 2.0, "away": 2.0}})
    assert blank["action"] == "SKIP" and blank["decision_grade"] is None
    # model 50% on a -110/-110 market: fair 50%, no edge, negative value at the offered price
    flat = engine.build_decision({"home_name": "A", "away_name": "B", "probabilities": {"home": 50, "away": 50},
                                  "odds": {"home": 1.909, "away": 1.909}, "data_quality": 85})
    assert flat["implied_probability"] == 0.5 and flat["action"] == "SKIP" and flat["decision_grade"] == "D"
    strong = engine.build_decision({"home_name": "A", "away_name": "B", "probabilities": {"home": 70, "away": 30},
                                    "odds": {"home": 1.7, "away": 2.3}, "data_quality": 85, "confidence": "Low"})
    assert strong["confidence"] == 70                          # the model's probability, not the "Low" = 45 label
    assert strong["action"] == "BET" and strong["expected_value"] == round(0.7 * 1.7 - 1, 4) and strong["decision_grade"] == "A"
    loose = engine._adaptive_adjustment({"trust_score": 90})
    assert loose["bet_min_confidence"] == 64                   # a hot streak never lowers the bar


def test_recorded_picks_keep_their_snapshot_and_closing_prices_sit_beside_them(tmp_path):
    db = tmp_path / "picks.sqlite3"
    leg = {"selection": "A Over 20.5 Points", "kickoff": "2026-11-05T00:30:00Z", "price": 1.9, "chance": 0.58, "book": "DraftKings",
           "fair": 0.5, "story": 0.3, "sport": "nba", "grade": {"kind": "player", "path": "basketball/nba", "event": "1",
                                                                "player": "A", "stat": "points", "line": 20.5, "side": "over"}}
    pt.record_tiers("2026-11-04", "morning", {"nba": {"medium": {"decimal_odds": 1.9, "hit_prob": .58, "tag": "+EV", "legs": [leg]}}}, location=db)
    pt.record_tiers("2026-11-04", "midday", {"nba": {"medium": {"decimal_odds": 2.4, "hit_prob": .5, "legs": [{**leg, "price": 2.4}]}}},
                    location=db)  # the same slip re-sent later the same day never overwrites what was first sent
    too_early = datetime(2026, 11, 4, 23, 0, tzinfo=UTC)
    assert pt.capture_closing(now=too_early, location=db, pricer=lambda f, k: 1.8) == 0
    near = datetime(2026, 11, 5, 0, 20, tzinfo=UTC)
    assert pt.capture_closing(now=near, location=db, pricer=lambda f, k: 1.8) == 1
    assert pt.capture_closing(now=datetime(2026, 11, 5, 0, 29, tzinfo=UTC), location=db, pricer=lambda f, k: 1.75) == 1
    with pt.db(db) as connection:
        rows = [dict(r) for r in connection.execute("SELECT price, close_price, book, fair, story, sport FROM legs")]
        tags = [r[0] for r in connection.execute("SELECT tag FROM parlays")]
    assert [(r["price"], r["close_price"]) for r in rows] == [(1.9, 1.75)]  # sent price kept; the last pregame reading wins
    assert rows[0]["book"] == "DraftKings" and rows[0]["fair"] == 0.5 and rows[0]["sport"] == "nba" and tags == ["+EV"]


def test_scorecard_reports_accuracy_bands_closing_value_and_return(tmp_path):
    db = tmp_path / "picks.sqlite3"
    def leg(name, chance, story):
        return {"selection": name, "kickoff": "2026-11-03T00:30:00Z", "price": 2.0, "chance": chance, "story": story, "sport": "nba",
                "grade": {"kind": "player", "stat": "points"}}
    pt.record_tiers("2026-11-02", "morning", {"nba": {"fly": {"decimal_odds": 8, "tag": "+EV",
                                                              "legs": [leg("A", .65, .4), leg("B", .65, 0), leg("C", .55, 0)]}}}, location=db)
    results = iter([("won", "30"), ("lost", "10"), ("won", "25")])
    pt.settle(now=datetime(2026, 11, 3, 10, tzinfo=UTC), location=db, grader=lambda f: next(results))
    with pt.db(db) as connection:
        connection.execute("UPDATE legs SET close_price=1.8")
    card = pt.scorecard(7, today=date(2026, 11, 4), location=db)
    everything = card["groups"]["All"]
    assert card["legs"] == 3 and everything["won"] == 2 and abs(everything["roi"] - (1 + 1 - 1) / 3) < 1e-9
    assert everything["beat_close"] == 1.0 and card["groups"]["Matchup story"]["n"] == 1
    assert card["groups"]["We said 60%–70%"]["n"] == 2 and card["groups"]["Slip tag: +EV"]["n"] == 3
    assert "Brier" in pt.describe(7, today=date(2026, 11, 4), location=db)


def test_nfl_legs_grade_from_espn_box_scores(tmp_path, monkeypatch):
    box = {"players": [{"statistics": [
        {"name": "passing", "keys": ["completions/passingAttempts", "passingYards"],
         "athletes": [{"athlete": {"displayName": "Joe Burrow"}, "stats": ["24/35", "281"]}]},
        {"name": "rushing", "keys": ["rushingAttempts", "rushingYards"],
         "athletes": [{"athlete": {"displayName": "Chase Brown"}, "stats": ["15", "64"]}]},
        {"name": "receiving", "keys": ["receptions", "receivingYards"],
         "athletes": [{"athlete": {"displayName": "Chase Brown"}, "stats": ["4", "31"]}]}]}]}
    monkeypatch.setattr("runtime_paths.cache_dir", lambda name: tmp_path)
    (tmp_path / "nfl-box-77.json").write_text(json.dumps(box), encoding="utf-8")
    assert pt.nfl_player_value("77", "Joe Burrow", "passing_yards") == 281
    assert pt.nfl_player_value("77", "Joe Burrow", "completions") == 24 and pt.nfl_player_value("77", "Joe Burrow", "attempts") == 35
    assert pt.nfl_player_value("77", "Chase Brown", "rushing_yards+receiving_yards") == 95
    assert pt.nfl_player_value("77", "Nobody", "receptions") is None


def test_first_published_pick_is_kept_when_revised(tmp_path, monkeypatch):
    import model_tracker as mt
    monkeypatch.setattr(mt, "_TRACKING_FILE", tmp_path / "tracking.json")
    first = mt.save_prediction("soccer", "Home", "Away", "A", {"a": 60, "b": 20, "draw": 20}, "High", game_date="2099-01-01",
                               kickoff="2099-01-01T20:00:00Z")
    mt.save_prediction("soccer", "Home", "Away", "B", {"a": 30, "b": 50, "draw": 20}, "Medium", game_date="2099-01-01",
                       kickoff="2099-01-01T20:00:00Z")
    stored = mt.get_prediction_by_id(first)
    assert stored["predicted_winner"] == "B" and stored["first_published"]["predicted_winner"] == "A" and stored["revisions"] == 1
    mt.update_prediction_result(first, "A", {"a": 2, "b": 0})
    stored = mt.get_prediction_by_id(first)
    assert stored["first_published"]["is_correct"] is True and stored["is_correct"] is False
