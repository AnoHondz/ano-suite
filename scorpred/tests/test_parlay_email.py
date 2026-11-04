from services import parlay_email


def _slate():
    pick = {"rank": 1, "selection": "<b>Boston</b> to win", "american": "-150", "book": "FanDuel", "prob": 0.62, "ev": 0.03,
            "tier": "value", "matchup": "Nets @ Celtics", "commence_time": "2026-11-03T00:00:00Z"}
    return {"sport": "nba", "label": "NBA", "title": "NBA · Mon Nov 2", "status": "ready",
            "picks": [pick, {**pick, "rank": 2, "selection": "Denver to win", "tier": "best available"}],
            "parlay": {"legs": [1, 2], "selections": ["<b>Boston</b> to win (-150)", "Denver to win (-150)"],
                       "american": "+125", "decimal_odds": 2.25, "hit_prob": 0.41, "payout_per_10": 22.5}}


def test_render_lists_picks_and_parlay_and_escapes_text():
    html = parlay_email.render([_slate()])
    assert "Parlay of the day: +125" in html and "Denver to win" in html and "best available, no edge" in html
    assert "<b>Boston</b>" not in html and "&lt;b&gt;Boston&lt;/b&gt;" in html
    assert "1-800-GAMBLER" in html


def test_player_parlay_uses_one_leg_per_game_and_pays_at_least_2x():
    def prop(name, match, chance, odds):
        return {"name": name, "team": "T", "side": "over", "line": 2.5, "label": "Threes", "chance": chance, "match_id": match,
                "kickoff": "2026-11-03T00:30:00Z", "sport": "nba", "market": {"decimal_odds": odds, "bookmaker": "FanDuel"}}
    report = {"generated_at": "2026-11-02T12:56:40+00:00", "props": {"nba": [
        prop("A", "g1", 0.60, 1.9), prop("B", "g1", 0.59, 1.9), prop("C", "g2", 0.55, 1.9)]}}
    parlay = parlay_email.player_parlay(report)
    assert [leg["selection"][0] for leg in parlay["legs"]] == ["A", "C"]  # A+B is the same game
    assert parlay["decimal_odds"] >= 2 and abs(parlay["hit_prob"] - 0.33) < 1e-9
    assert "Player prop parlay" in parlay_email.render_player_parlay(parlay)
    assert parlay_email.player_parlay({"props": {"nba": [prop("A", "g1", 0.6, 1.9)]}}) is None


class _App:
    import contextlib
    app_context = staticmethod(contextlib.nullcontext)


def test_daily_parlays_cover_every_sport_with_a_slate_and_one_across_sports(monkeypatch):
    import parlay_engine as pe
    soccer = {**_slate(), "sport": "soccer", "label": "Soccer",
              "picks": [{**p, "selection": "Arsenal to win", "prob": 0.7, "price": 1.5} for p in _slate()["picks"][:1]]}
    nba = {**_slate(), "picks": [{**p, "price": 1.45} for p in _slate()["picks"]]}
    slates = {"nba": nba, "soccer": soccer}
    monkeypatch.setattr(pe, "slate_window", lambda sport, day: None if sport == "nfl" else object())
    monkeypatch.setattr(pe, "ensure_slate", lambda sport, day, **kw: slates[sport])
    result = parlay_email.daily_parlays(_App())
    assert [s["sport"] for s in result["slates"]] == ["nba", "soccer"]  # NFL has no slate today
    assert result["combined"] and result["combined"]["decimal_odds"] >= 2
    html = parlay_email.render_section(result)
    assert "NBA +125" in html and "All sports" in html and "Arsenal to win" in html


def test_a_parlay_failure_never_blocks_the_morning_email(monkeypatch):
    import parlay_engine as pe
    monkeypatch.setattr(pe, "slate_window", lambda sport, day: object())
    def boom(*a, **k):
        raise pe.OddsApiError("This month's odds-credit allowance for Parlay Mode is used up")
    monkeypatch.setattr(pe, "ensure_slate", boom)
    result = parlay_email.daily_parlays(_App())
    assert result["slates"] == [] and "allowance" in result["notes"][0]
    assert "used up" in parlay_email.render_section(result)


def test_player_parlay_section_shows_each_tier_and_hides_when_empty():
    leg = {"selection": "James Harden Over 11.5 Points", "american": "-556", "book": "DraftKings", "chance": 0.9, "price": 1.18,
           "last10": "10/10", "record": "15/15", "average": 18.8, "model": None, "matchup": "OKC @ CLE", "game_id": "g1"}
    parlay = {"legs": [leg, {**leg, "game_id": "g2", "selection": "Jarrett Allen Over 7.5 Points"}], "decimal_odds": 1.52,
              "hit_prob": 0.76, "payout_per_10": 15.2}
    html = parlay_email.render_tiers_section({"nba": {"low": parlay, "medium": None, "fly": None}})
    assert "Player parlays" in html and "Easy · low-variance parlay" in html and "last 15: 15/15" in html and "Medium" in html  # older legs
    explained = {**parlay, "legs": [{**leg, "why": ["Harden has had 12+ points in 15 of his last 15 games, averaging 18.8.",
                                                   "Cavaliers allow 25.1 pts a game to guards, the 2nd-most of 30 teams (+12% vs average)."]}]}
    html = parlay_email.render_tiers_section({"nba": {"low": explained}})
    assert "Harden has had 12+ points in 15 of his last 15 games, averaging 18.8. Cavaliers allow" in html
    assert "We make it 90% to hit; the bookmaker&#x27;s price implies 81% once its margin is removed." in html
    assert parlay_email.render_tiers_section({"nba": {"low": None, "medium": None, "fly": None}}) == ""
    tagged = parlay_email.render_tiers_section({"nba": {"low": {**explained, "tag": "+EV"}, "medium": {**explained, "tag": "Best available"}}})
    assert "+EV" in tagged and "Best available, no edge found" in tagged


def test_midday_player_parlays_send_once_and_only_with_parlays(monkeypatch, tmp_path):
    from services import daily_digest
    monkeypatch.setattr(daily_digest, "settings", lambda: {"enabled": True, "recipient": "me@example.com"})
    monkeypatch.setattr(daily_digest, "root", lambda: tmp_path)
    sent = []
    sender = lambda subject, html, to, before: sent.append((subject, to)) or "<id>"
    empty = lambda app: {"tiers": {"low": None, "medium": None, "fly": None}, "games": 0}
    assert parlay_email.send_player_parlays(None, builders={"nba": empty, "soccer": empty}, sender=sender)["state"] == "nothing_to_send"
    assert sent == []
    leg = {"selection": "A Over 7.5 Points", "american": "-300", "book": "DraftKings", "chance": 0.8, "price": 1.33,
           "matchup": "X @ Y", "game_id": "g1"}
    full = lambda app: {"tiers": {"low": {"legs": [leg, {**leg, "game_id": "g2"}], "decimal_odds": 1.77, "hit_prob": 0.64,
                                          "payout_per_10": 17.7}, "medium": None, "fly": None}, "generated_at": "2026-11-04T17:00:00+00:00"}
    result = parlay_email.send_player_parlays(None, builders={"nba": empty, "soccer": full}, sender=sender)
    assert result == {"state": "sent", "sports": ["soccer"]}  # only sports with a parlay are included
    assert parlay_email.send_player_parlays(None, builders={"soccer": full}, sender=sender)["state"] == "already_sent"  # 3 PM retry
    assert len(sent) == 1 and sent[0][1] == "me@example.com" and "SOCCER player parlays" in sent[0][0]


def test_typed_commands_send_to_the_configured_recipient_and_soccer_falls_back_to_record_picks(monkeypatch):
    from services import daily_digest, tiered_parlays
    monkeypatch.setattr(daily_digest, "settings", lambda: {"enabled": True, "recipient": "me@example.com"})
    sent = []
    sender = lambda subject, html, to, before: sent.append((subject, to, html)) or "<id>"
    monkeypatch.setattr(tiered_parlays, "build_soccer", lambda app=None, *, now=None, unpriced=False: (
        {"picks": [{"selection": "Kane 2+ Shots", "team": "Bayern", "last10": "9/10", "record": "14/15", "average": 3.1,
                    "matchup": "A @ B", "chance": 0.88, "game_id": "g1"}], "games": 1, "generated_at": "2026-11-03T11:00:00+00:00"}
        if unpriced else {"tiers": {}, "games": 0}))
    result = parlay_email.run_command(None, "soccer_player_parlays", sender=sender)
    assert result == {"state": "sent", "sent": ["soccer picks"]}
    assert sent[0][1] == "me@example.com" and "no prices posted yet" in sent[0][0] and "Kane 2+ Shots" in sent[0][2]
    import pytest
    with pytest.raises(ValueError):
        parlay_email.run_command(None, "anything else", sender=sender)


def test_send_goes_only_to_the_configured_recipient(monkeypatch):
    from services import daily_digest
    monkeypatch.setattr(daily_digest, "settings", lambda: {"recipient": "me@example.com"})
    sent = []
    result = parlay_email.send([_slate()], sender=lambda subject, html, to, before: sent.append((subject, to)) or "<id>")
    assert sent[0][1] == "me@example.com" and "Parlay Mode" in sent[0][0] and result["slates"] == ["nba"]
