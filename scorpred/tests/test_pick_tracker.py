from datetime import UTC, date, datetime
from unittest.mock import patch

from services import pick_tracker as pt


def leg(selection, grade, price=1.5, kickoff="2026-11-02T23:30:00Z"):
    return {"selection": selection, "kickoff": kickoff, "price": price, "chance": 0.7, "grade": grade}


def test_record_settle_and_recap_show_a_clean_sweep(tmp_path):
    db = tmp_path / "picks.sqlite3"
    parlay = {"decimal_odds": 1.8, "hit_prob": 0.6, "legs": [leg("Brunson Over 6.5 Assists", {"kind": "player"}),
                                                            leg("Embiid Over 24.5 Points", {"kind": "player"})]}
    pt.record_tiers("2026-11-02", "morning", {"nba": {"low": parlay, "fly": None}}, location=db)
    pt.record_tiers("2026-11-02", "midday", {"nba": {"low": parlay}}, location=db)          # same parlay: kept once
    assert len(pt.parlays("2026-11-02", "2026-11-02", location=db)) == 1
    early = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)
    assert pt.settle(now=early, location=db, grader=lambda f: ("won", "9")) == 0            # games not over yet
    later = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
    assert pt.settle(now=later, location=db, grader=lambda f: ("won", "9")) == 2
    text = pt.describe(1, today=date(2026, 11, 3), location=db)
    assert "Clean sweep" in text and "NBA Easy parlay (paid 1.80x): won" in text and "up $0.80 if you’d put $1 on each" in text
    html = pt.render_recap(today=date(2026, 11, 3), location=db)
    assert "Yesterday's results" in html and "Clean sweep" in html


def test_one_lost_leg_loses_the_parlay_and_unknown_games_are_voided_after_three_days(tmp_path):
    db = tmp_path / "picks.sqlite3"
    parlay = {"decimal_odds": 3.0, "hit_prob": 0.3, "legs": [leg("A", {"kind": "player"}), leg("B", {"kind": "player"})]}
    pt.record_tiers("2026-11-02", "morning", {"soccer": {"medium": parlay}}, location=db)
    results = iter([("won", "2"), ("lost", "0")])
    pt.settle(now=datetime(2026, 11, 3, 10, tzinfo=UTC), location=db, grader=lambda f: next(results))
    assert pt.parlays("2026-11-02", "2026-11-02", location=db)[0]["result"] == "lost"
    pt.record_tiers("2026-11-02", "morning", {"soccer": {"fly": {"decimal_odds": 6, "legs": [leg("C", {"kind": "player"})]}}}, location=db)
    pt.settle(now=datetime(2026, 11, 7, tzinfo=UTC), location=db, grader=lambda f: (None, None))
    fly = [p for p in pt.parlays("2026-11-02", "2026-11-02", location=db) if p["tier"] == "fly"][0]
    assert fly["result"] == "void"


def summary(home, away, completed=True):
    return {"completed": completed, "competitors": [{"id": "1", "name": "Arsenal", "homeAway": "home", "score": home},
                                                    {"id": "2", "name": "Chelsea", "homeAway": "away", "score": away}]}


def test_grading_rules_from_espn_results():
    with patch.object(pt, "_summary", return_value=summary(2, 1)):
        assert pt.grade({"kind": "btts", "path": "soccer/eng.1", "event": "9", "side": "yes"})[0] == "won"
        assert pt.grade({"kind": "total", "path": "soccer/eng.1", "event": "9", "line": 2.5, "side": "over"}) == ("won", "3 total")
        assert pt.grade({"kind": "team_total", "path": "soccer/eng.1", "event": "9", "team_id": "2", "line": 1.5, "side": "under"})[0] == "won"
        assert pt.grade({"kind": "moneyline", "path": "soccer/eng.1", "event": "9", "outcome": "Chelsea"})[0] == "lost"
        assert pt.grade({"kind": "spread", "path": "soccer/eng.1", "event": "9", "outcome": "Chelsea", "point": 1.0})[0] == "push"
    with patch.object(pt, "_summary", return_value=summary(None, None, completed=False)):
        assert pt.grade({"kind": "btts", "path": "soccer/eng.1", "event": "9", "side": "yes"}) == (None, None)
    box = {"teams": {"1": {"players": [{"name": "Jalen Brunson", "points": 30, "rebounds": 4, "assists": 9}]}}}
    with patch.object(pt, "_summary", return_value=summary(110, 100)), patch("services.espn_box_scores.box_score", return_value=box):
        facts = {"kind": "player", "path": "basketball/nba", "event": "9", "player": "Jalen Brunson", "line": 6.5, "side": "over"}
        assert pt.grade({**facts, "stat": "assists"}) == ("won", "9")
        assert pt.grade({**facts, "stat": "pra", "line": 45.5}) == ("lost", "43")
        assert pt.grade({**facts, "player": "Somebody Else", "stat": "points"}) == ("void", "did not play")


def test_older_legs_get_grading_facts_from_their_text():
    nba = {"selection": "Jalen Brunson Over 6.5 Assists", "game_id": "espn-401", "sport": "nba"}
    assert pt.infer_grade(nba) == {"kind": "player", "path": "basketball/nba", "event": "401", "player": "Jalen Brunson",
                                   "stat": "assists", "line": 6.5, "side": "over"}
    assert pt.infer_grade({"selection": "Bukayo Saka 2+ Shots", "team": "Arsenal", "sport": "soccer"})["line"] == 1.5
    soccer = {"game_id": "espn-77", "sport": "soccer"}
    assert pt.infer_grade({**soccer, "selection": "Both teams to score: No"}) == {"kind": "btts", "event": "77", "side": "no"}
    assert pt.infer_grade({**soccer, "selection": "Chelsea under 1.5 goals"})["team_name"] == "Chelsea"
    with patch.object(pt, "_summary", side_effect=lambda path, event: summary(0, 2) if path == "soccer/esp.1" else {"competitors": []}):
        assert pt.grade({"kind": "team_total", "event": "77", "team_name": "Chelsea", "line": 1.5, "side": "under"}) == ("lost", "2")


def test_game_line_parlays_are_recorded_with_their_legs(tmp_path):
    db = tmp_path / "picks.sqlite3"
    picks = [{"selection": "Knicks to win", "american": "-150", "commence_time": "2026-11-02T23:30:00Z", "price": 1.67, "prob": 0.6,
              "market": "h2h", "event_id": "espn-401", "sport_key": "basketball_nba", "outcome": "New York Knicks", "home": "x", "away": "y"},
             {"selection": "Over 220.5", "american": "-110", "commence_time": "2026-11-02T23:30:00Z", "price": 1.9, "prob": 0.55,
              "market": "totals", "event_id": "espn-402", "sport_key": "basketball_nba", "outcome": "Over", "point": 220.5}]
    slate = {"sport": "nba", "picks": picks, "parlay": {"selections": ["Knicks to win (-150)", "Over 220.5 (-110)"], "decimal_odds": 3.17}}
    pt.record_sent("2026-11-02", "morning", parlays={"slates": [slate]}, location=db)
    row = pt.parlays("2026-11-02", "2026-11-02", location=db)[0]
    assert row["kind"] == "game_parlay" and len(row["legs"]) == 2
