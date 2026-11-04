from __future__ import annotations

from datetime import date

import pytest

import parlay_engine as pe


def _book(title, h2h=None, spreads=None, totals=None):
    markets = []
    if h2h:
        markets.append({"key": "h2h", "outcomes": [{"name": n, "price": p} for n, p in h2h]})
    if spreads:
        markets.append({"key": "spreads", "outcomes": [{"name": n, "price": p, "point": pt} for n, p, pt in spreads]})
    if totals:
        markets.append({"key": "totals", "outcomes": [{"name": n, "price": p, "point": pt} for n, p, pt in totals]})
    return {"title": title, "markets": markets}


def _event(eid, home, away, books):
    return {"id": eid, "home_team": home, "away_team": away,
            "commence_time": "2026-11-03T00:00:00Z", "bookmakers": books}


def _nba_event(eid="e1", home="Boston Celtics", away="Brooklyn Nets"):
    return _event(eid, home, away, [
        _book("DraftKings", h2h=[(home, 1.30), (away, 3.60)],
              spreads=[(home, 1.91, -8.5), (away, 1.91, 8.5)],
              totals=[("Over", 1.91, 221.5), ("Under", 1.91, 221.5)]),
        _book("FanDuel", h2h=[(home, 1.33), (away, 3.40)],
              spreads=[(home, 1.95, -8.5), (away, 1.87, 8.5)],
              totals=[("Over", 1.87, 221.5), ("Under", 1.95, 221.5)]),
    ])


# ── math ─────────────────────────────────────────────────────────────────────

def test_american_conversion():
    assert pe.american(2.5) == "+150"
    assert pe.american(1.5) == "-200"
    assert pe.american(2.0) == "+100"
    assert pe.american(1.0) == "—"


def test_build_legs_best_price_and_devig():
    legs = pe.build_legs(_nba_event(), "basketball_nba")
    by = {(l["market"], l["outcome"], l["point"]): l for l in legs}
    fav = by[("h2h", "Boston Celtics", None)]
    assert fav["price"] == 1.33 and fav["book"] == "FanDuel"
    assert fav["books_count"] == 2
    # de-vigged probabilities of both sides of a market sum to ~1
    dog = by[("h2h", "Brooklyn Nets", None)]
    assert abs(fav["consensus_prob"] + dog["consensus_prob"] - 1) < 1e-3
    over = by[("totals", "Over", 221.5)]
    under = by[("totals", "Under", 221.5)]
    assert abs(over["consensus_prob"] + under["consensus_prob"] - 1) < 1e-3
    assert over["selection"].startswith("Over 221.5")
    assert by[("spreads", "Boston Celtics", -8.5)]["selection"] == "Boston Celtics -8.5"


def test_model_blend_only_on_moneyline():
    legs = pe.build_legs(_nba_event(), "basketball_nba", {"home": 0.90, "away": 0.10})
    ml = next(l for l in legs if l["market"] == "h2h" and l["outcome"] == "Boston Celtics")
    sp = next(l for l in legs if l["market"] == "spreads")
    assert ml["source"] == "model+market" and ml["model_prob"] == 0.9
    assert ml["prob"] > ml["consensus_prob"]
    assert sp["source"] == "market" and sp["model_prob"] is None


def test_min_books_filter():
    ev = _event("x", "A", "B", [_book("Solo", h2h=[("A", 1.5), ("B", 2.6)])])
    assert pe.build_legs(ev, "basketball_nba") == []
    assert len(pe.build_legs(ev, "basketball_nba", min_books=1)) == 2


# ── selection & parlay ───────────────────────────────────────────────────────

def _leg(eid, prob, price, sel=None):
    return {"event_id": eid, "prob": prob, "price": price, "ev": round(prob * price - 1, 4),
            "selection": sel or f"pick {eid}", "american": pe.american(price)}


def test_select_picks_one_per_game_and_top_five():
    legs = [_leg(f"g{i}", 0.60 + i * 0.01, 1.70) for i in range(8)]
    legs.append(_leg("g7", 0.99, 1.70))   # a second leg on game g7 must not double up
    picks = pe.select_picks(legs)
    assert len(picks) == 5
    assert len({p["event_id"] for p in picks}) == 5
    assert [p["rank"] for p in picks] == [1, 2, 3, 4, 5]


def test_select_picks_skips_heavy_chalk_and_never_calls_negative_value_a_value_pick():
    legs = [_leg("a", 0.95, 1.05), _leg("b", 0.60, 1.40), _leg("c", 0.60, 1.75)]
    picks = pe.select_picks(legs)
    assert [p["event_id"] for p in picks] == ["c", "b"]          # chalk (a) never; b only fills the thin card
    assert {p["event_id"]: p["tier"] for p in picks} == {"c": "value", "b": "best available"}


def test_select_picks_relaxes_floor_to_fill():
    legs = [_leg("a", 0.56, 1.85), _leg("b", 0.52, 1.95)]
    assert {p["event_id"] for p in pe.select_picks(legs)} == {"a", "b"}


def test_parlay_pays_at_least_2x_and_maximises_expected_value():
    picks = [_leg("a", 0.75, 1.40), _leg("b", 0.72, 1.45), _leg("c", 0.60, 1.80), _leg("d", 0.55, 1.90)]
    for i, p in enumerate(picks, 1):
        p["rank"] = i
    parlay = pe.build_parlay(picks)
    assert parlay["decimal_odds"] >= 2.0
    assert parlay["legs"] == [1, 2, 3, 4]              # every leg is +4% or better, so all four compound the value
    assert parlay["tag"] == "+EV" and parlay["american"].startswith("+")
    assert parlay["payout_per_10"] == pytest.approx(69.43, abs=0.01)


def test_parlay_from_non_value_picks_is_tagged_best_available():
    picks = [_leg("a", 0.68, 1.45), _leg("b", 0.66, 1.45)]
    for i, p in enumerate(picks, 1):
        p["rank"] = i
    parlay = pe.build_parlay(picks)
    assert parlay["tag"] == "Best available" and parlay["ev"] < 0.04


def test_parlay_none_when_odds_too_short():
    picks = [_leg("a", 0.9, 1.10), _leg("b", 0.9, 1.10)]
    for i, p in enumerate(picks, 1):
        p["rank"] = i
    assert pe.build_parlay(picks) is None


# ── schedules ────────────────────────────────────────────────────────────────

def test_schedules_with_free_espn_odds_cover_every_game_day(monkeypatch):
    mon, fri = date(2026, 11, 2), date(2026, 11, 6)
    monkeypatch.delenv("PARLAY_ODDS_SOURCE", raising=False)
    assert pe.slate_window("nba", mon).kind == "daily"
    assert pe.slate_window("nfl", mon).kind == "daily"  # Monday Night Football gets a slate
    w = pe.slate_window("soccer", fri)
    assert w.kind == "daily" and "soccer_epl" in w.sport_keys and "soccer_uefa_champs_league" in w.sport_keys
    assert (w.end - w.start).days == 1


def test_credit_using_feed_still_rations_soccer(monkeypatch):
    monkeypatch.setenv("PARLAY_ODDS_SOURCE", "odds_api")
    w = pe.slate_window("soccer", date(2026, 11, 6))
    assert w.kind == "weekend" and (w.end - w.start).days == 3
    tue = pe.slate_window("soccer", date(2026, 11, 3))
    assert tue.kind == "european" and tue.sport_keys == pe.SOCCER_EURO_KEYS


# ── team matching ────────────────────────────────────────────────────────────

def test_match_model_rows_handles_names_and_swapped_sides():
    events = [{"id": "e1", "home_team": "Manchester City", "away_team": "Brighton and Hove Albion"}]
    rows = [{"home": "Man City FC", "away": "Brighton", "p_home": 62, "p_away": 15, "p_draw": 23}]
    # "Man City" won't token-match "Manchester City" but "City" + substring rules handle common forms
    got = pe.match_model_rows(events, rows)
    assert got["e1"]["home"] == pytest.approx(0.62)
    swapped = [{"home": "Brighton & Hove Albion", "away": "Manchester City", "p_home": 0.2, "p_away": 0.6}]
    got = pe.match_model_rows(events, swapped)
    assert got["e1"]["home"] == 0.6 and got["e1"]["away"] == 0.2


# ── end to end with a fake client ────────────────────────────────────────────

class FakeClient(pe.OddsClient):
    def __init__(self, events_by_key):
        super().__init__(api_key="test")
        self.events_by_key = events_by_key
        self.odds_calls = []

    def events(self, sport_key, start, end):
        return [{"id": e["id"]} for e in self.events_by_key.get(sport_key, [])]

    def odds(self, sport_key, start, end):
        self.odds_calls.append(sport_key)
        return self.events_by_key.get(sport_key, [])


def test_generate_and_store_slate(tmp_path):
    teams = [("Boston Celtics", "Brooklyn Nets"), ("Denver Nuggets", "Utah Jazz"),
             ("Miami Heat", "Orlando Magic"), ("Phoenix Suns", "Sacramento Kings"),
             ("Dallas Mavericks", "San Antonio Spurs"), ("New York Knicks", "Chicago Bulls")]
    evs = [_nba_event(f"e{i}", h, a) for i, (h, a) in enumerate(teams)]
    client = FakeClient({"basketball_nba": evs})
    store = pe.SlateStore(tmp_path)

    calls = []

    def provider(sport, events):
        calls.append(sport)
        return pe.match_model_rows(events, [{"home": "Celtics", "away": "Nets", "p_home": 80, "p_away": 20}])

    day = date(2026, 11, 2)
    slate = pe.ensure_slate("nba", day, client=client, store=store, model_provider=provider)
    assert slate["status"] == "ready"
    assert len(slate["picks"]) == 5
    assert slate["parlay"]["decimal_odds"] >= 2.0
    assert slate["model_games"] == 1
    assert store.load("nba", day)["slate_date"] == "2026-11-02"

    # second call reuses the saved slate — no new odds calls
    pe.ensure_slate("nba", day, client=client, store=store, model_provider=provider)
    assert client.odds_calls == ["basketball_nba"]


def test_a_day_without_games_spends_nothing(tmp_path):
    client = FakeClient({})
    slate = pe.ensure_slate("soccer", date(2026, 11, 2), client=client, store=pe.SlateStore(tmp_path))
    assert slate["status"] == "no_games"
    assert client.odds_calls == []


def test_off_day_returns_stub_without_saving(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "slate_window", lambda sport, day: None)
    store = pe.SlateStore(tmp_path)
    slate = pe.ensure_slate("nfl", date(2026, 11, 2), client=FakeClient({}), store=store)
    assert slate["status"] == "off_day"
    assert store.load("nfl", date(2026, 11, 2)) is None


class BrokenClient(FakeClient):
    def events(self, sport_key, start, end):
        raise ConnectionError("network down")


def test_network_failure_is_not_saved(tmp_path):
    store = pe.SlateStore(tmp_path)
    slate = pe.ensure_slate("nba", date(2026, 11, 2), client=BrokenClient({}), store=store)
    assert slate["status"] == "error"
    assert store.load("nba", date(2026, 11, 2)) is None


# ── free ESPN odds ───────────────────────────────────────────────────────────

def _espn(home="Cleveland Cavaliers", away="Oklahoma City Thunder", draw=None, state="pre"):
    odds = {"provider": {"name": "DraftKings"},
            "moneyline": {"home": {"close": {"odds": "-179"}}, "away": {"close": {"odds": "+150"}}},
            "pointSpread": {"home": {"close": {"line": "-3.5", "odds": "-118"}}, "away": {"close": {"line": "+3.5", "odds": "-102"}}},
            "total": {"over": {"close": {"line": "o229.5", "odds": "-106"}}, "under": {"close": {"line": "u229.5", "odds": "-114"}}}}
    if draw:
        odds["drawOdds"] = {"moneyLine": draw}
    return {"id": "401", "date": "2026-11-03T00:00Z", "status": {"type": {"state": state}},
            "competitions": [{"competitors": [{"homeAway": "home", "team": {"displayName": home}},
                                              {"homeAway": "away", "team": {"displayName": away}}], "odds": [odds]}]}


def test_espn_event_becomes_one_book_with_moneyline_spread_and_total():
    ev = pe.espn_event(_espn())
    book = ev["bookmakers"][0]
    assert ev["home_team"] == "Cleveland Cavaliers" and book["title"] == "DraftKings"
    markets = {m["key"]: m["outcomes"] for m in book["markets"]}
    assert markets["h2h"][0]["price"] == pytest.approx(1.5587, abs=1e-3) and markets["h2h"][1]["price"] == 2.5
    assert markets["spreads"][0]["point"] == -3.5 and markets["totals"][1] == {"name": "Under", "price": pytest.approx(1.8772, abs=1e-3), "point": 229.5}
    legs = pe.build_legs(ev, "basketball_nba", min_books=1)
    assert {l["market"] for l in legs} == {"h2h", "spreads", "totals"} and all(l["book"] == "DraftKings" for l in legs)
    soccer = pe.espn_event(_espn("Newcastle United", "Everton", draw=250))
    assert [o["name"] for o in soccer["bookmakers"][0]["markets"][0]["outcomes"]] == ["Newcastle United", "Everton", "Draw"]


def test_espn_client_spends_no_credits_and_skips_started_games():
    class Session:
        def get(self, url, params, timeout):
            assert "site.api.espn.com" in url and "basketball/nba" in url
            return type("R", (), {"status_code": 200, "json": lambda self: {"events": [_espn(), _espn(state="in")]}})()
    client = pe.EspnOddsClient(session=Session())
    window = pe.slate_window("nba", date(2026, 11, 2))
    assert len(client.odds("basketball_nba", window.start, window.end)) == 1 and client.min_books == 1
    assert pe.american_to_decimal("-110") == pytest.approx(1.9091, abs=1e-3) and pe.american_to_decimal("EVEN") == 2.0


# ── shared odds-credit allowance ─────────────────────────────────────────────

class NoNetwork:
    def get(self, *args, **kwargs):
        raise AssertionError("no request may be sent once the allowance is used up")


def test_odds_call_reserves_three_credits_and_refuses_when_allowance_is_used_up():
    asked = []
    client = pe.OddsClient(api_key="k", session=NoNetwork(), reserve=lambda cost, key: asked.append(cost) or False)
    with pytest.raises(pe.OddsApiError, match="allowance"):
        client.odds("basketball_nba", *pe._et_day_bounds(date(2026, 11, 2)))
    assert asked == [3]  # h2h + spreads + totals in one region


def test_parlay_share_and_shared_monthly_cap(monkeypatch):
    from services import cache_service, prop_markets
    store = {}
    monkeypatch.setattr(cache_service, "get_json", lambda key, ttl=None: store.get(key))
    monkeypatch.setattr(cache_service, "set_json", lambda key, value, ttl=None: store.__setitem__(key, value))
    reserve = lambda: prop_markets.reserve_credits(3, credential="k", share="parlay", share_cap=6)
    assert reserve() and reserve() and not reserve()           # Parlay Mode stops at its own share
    assert prop_markets.credit_usage(credential="k") == 6       # and its spend counts toward the shared cap
    assert prop_markets.credit_usage(credential="k", share="parlay") == 6
    monkeypatch.setattr(prop_markets, "MONTHLY_CREDIT_CAP", 8)
    assert not prop_markets.reserve_credits(3, credential="k")  # the shared cap binds every feature
    # Credits the provider counted elsewhere raise the local total; a lower figure never lowers it.
    prop_markets.note_provider_usage("400", credential="k")
    prop_markets.note_provider_usage("5", credential="k")
    assert prop_markets.credit_usage(credential="k") == 400


def test_match_odds_stop_at_the_monthly_allowance(monkeypatch):
    import odds_fetcher
    from services import prop_markets
    monkeypatch.setattr(odds_fetcher, "_LEAGUE_GAMES", {})
    monkeypatch.setattr(prop_markets, "reserve_credits", lambda *a, **k: False)
    monkeypatch.setattr(odds_fetcher.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))
    assert odds_fetcher._league_games("soccer_epl", "h2h", "uk,eu,us") is None
