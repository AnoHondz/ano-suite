"""Daily delivery contracts; no real email, network, models or sports stores."""
import json
from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from filelock import FileLock

from services import daily_digest as digest


@pytest.fixture(autouse=True)
def private_digest(monkeypatch, tmp_path):
    monkeypatch.setattr(digest, "root", lambda: tmp_path / "digest")
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASS", raising=False)


def fresh(now):
    return {"state": "fresh", "observed_at": now.isoformat(), "expires_at": (now + timedelta(hours=2)).isoformat()}


def fixture(sport, now, i=1):
    return {"sport": sport, "matchId": str(i), "teamA": "Home", "teamB": "Away", "homeId": "1", "awayId": "2",
            "leagueId": 39 if sport == "soccer" else None, "matchDate": (now + timedelta(hours=5)).isoformat(),
            "eventStatus": "pre", "side": "Home", "action": "CONSIDER", "confidence": 75,
            "winProbability": None if sport == "nfl" else 60, "scenarioShare": 70 if sport == "nfl" else None,
            "reason": "Existing API explanation.", "data": "Partial Data", "dataFreshness": fresh(now),
            "lineupEvidence":{**fresh(now),"status":"confirmed"},"injuryEvidence":{**fresh(now),"coverage_complete":True},
            "market":{"home_decimal":2.0,"away_decimal":2.0,"updated_at":now.isoformat()}}


def priced_pick(now, *, side="under", over_odds=2.1, under_odds=1.75, quotes=True):
    """A model pick made at the player's median line (under 2.5) with a fresh book line at 3.5."""
    quote = {"side": side, "line": 3.5, "over_decimal": over_odds, "under_decimal": under_odds, "model_chance": .5,
             "decimal_odds": under_odds, "bookmaker": "Controlled test book", "updated_at": (now - timedelta(minutes=1)).isoformat(),
             "expires_at": (now + timedelta(hours=2)).isoformat()}
    return {"name": "Healthy Player", "team": "Home", "side": side, "line": 2.5, "label": "Rebounds", "chance": .65,
            "probability": .65, "edge": .15, "games": 12, "ladder": {"start": 0, "over": [.97, .9, .8, .62, .4, .2, .1]},
            "sportsbook_quotes": [quote] if quotes else []}


def readers(now, *, count=1, started=False, stale=False, skip=False, wrong_roster=False):
    calls = []
    def api(path, params):
        calls.append(path)
        if "/dashboard/" in path:
            sport = path.split("/")[-1]
            cards = [fixture(sport, now, i) for i in range(1, count + 1)]
            if started:
                for card in cards:
                    card["matchDate"] = (now - timedelta(seconds=1)).isoformat()
            if stale:
                for card in cards:
                    card["dataFreshness"]["expires_at"] = (now - timedelta(seconds=1)).isoformat()
            if skip:
                for card in cards:
                    card["action"] = "SKIP"
            return {"slate": cards}, 200
        return {"result": {"projected_score": {"home": 2.1, "away": 1.2}, "model": "Existing model",
                           "most_likely_score": {"home": 2, "away": 1, "prob": 12.3}, "data_freshness": fresh(now)}}, 200
    def props(sport, card, day):
        calls.append("props:" + sport)
        result = {"picks": [priced_pick(now)]}
        if sport == "soccer" and not wrong_roster:  # soccer props verify their own rosters
            result["roster_verification"] = {**fresh(now), "status": "verified"}
        return {"result": result}, 200
    def roster(*args):
        return [{"displayName": "Different Player" if wrong_roster else "Healthy Player", "status": {"type": "active"}}]
    return api, props, roster, calls


def collect(now, **kwargs):
    api, props, roster, calls = readers(now, **kwargs)
    report = digest.collect(api, props, now=now, clock=lambda: now, leagues=[39], roster_loader=roster,
                            injury_loader=lambda sport: ([], fresh(now)))
    return report, calls


def empty_report():
    now = datetime.now(UTC)
    return {"generated_at": now.isoformat(), "window_end": (now + timedelta(hours=72)).isoformat(),
            "matches": {sport: [] for sport in digest.SPORTS}, "props": {sport: [] for sport in digest.SPORTS},
            "notes": ["No qualified data."], "match_count": 0}


def test_digest_retries_warming_soccer_once_without_inventing_forecasts(monkeypatch):
    now=datetime(2026,10,28,10,tzinfo=UTC)
    monkeypatch.setattr(digest,'settings',lambda:{**digest.DEFAULTS,'lookahead_hours':12})
    api, props, roster, calls=readers(now)
    soccer_calls=[]
    def warming(path,params):
        if path=='/api/dashboard/soccer':
            soccer_calls.append(dict(params))
            if len(soccer_calls)==1:
                return {'refresh_pending':True,'slate':[],'error':'still refreshing'},503
        return api(path,params)
    report=digest.collect(warming,props,now=now,clock=lambda:now,leagues=[39],roster_loader=roster,
                          injury_loader=lambda sport:([],fresh(now)))
    assert len(soccer_calls)==2
    assert len(report['matches']['soccer'])==1


def test_digest_stops_after_one_warming_retry(monkeypatch):
    now=datetime(2026,10,28,10,tzinfo=UTC)
    monkeypatch.setattr(digest,'settings',lambda:{**digest.DEFAULTS,'lookahead_hours':12})
    calls=[]
    def warming(path,params):
        if path=='/api/dashboard/soccer':
            calls.append(path)
            return {'refresh_pending':True,'slate':[]},503
        return {'slate':[]},200
    report=digest.collect(warming,lambda *a:({},200),now=now,clock=lambda:now,leagues=[39])
    assert len(calls)==2
    assert report['matches']['soccer']==[]
    assert any('one retry' in note for note in report['notes'])


def mail_ready(monkeypatch):
    digest.configure("recipient@example.com")
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASS", "private-test-secret")


def test_disabled_by_default_and_missing_credentials_do_not_send_or_collect():
    collector = Mock()
    sender = Mock()
    assert digest.run(None, collector=collector, sender=sender)["state"] == "disabled"
    digest.configure("recipient@example.com")
    assert digest.run(None, collector=collector, sender=sender)["state"] == "blocked_missing_smtp_credentials"
    collector.assert_not_called()
    sender.assert_not_called()


@pytest.mark.parametrize("recipient,time", [("one@example.com\nBcc: other@example.com", "13:00"),
                                           ("one@example.com,two@example.com", "13:00"), ("one@example.com", "25:00")])
def test_invalid_recipient_and_schedule_are_rejected(recipient, time):
    with pytest.raises(ValueError):
        digest.configure(recipient, time)


def test_up_to_five_each_no_duplicate_matches_and_distinct_measure_labels():
    now = datetime.now(UTC)
    report, calls = collect(now, count=7)
    assert report["match_count"] == 15
    for sport in digest.SPORTS:
        assert len(report["matches"][sport]) == 5
        assert len({r["match_id"] for r in report["matches"][sport]}) == 5
    assert report["matches"]["nfl"][0]["measure"]["label"].startswith("Descriptive scenario share")
    assert report["matches"]["nba"][0]["measure"]["label"] == "Win probability"
    assert report["matches"]["soccer"][0]["most_likely_score"]["prob"] == 12.3
    assert all("/research" not in path and "evaluation" not in path for path in calls)
    assert len(report["props"]["nba"]) == 1  # Same player is never padded across multiple games.


@pytest.mark.parametrize("condition", ["started", "stale"])
def test_insufficient_valid_data_never_gets_padded(condition):
    report, calls = collect(datetime.now(UTC), **{condition: True})
    assert report["match_count"] == 0
    assert all(not rows for rows in report["props"].values())
    assert any("0 of 5" in note for note in report["notes"])
    assert not any("/scoreline/" in path for path in calls)


def test_roster_mismatch_does_not_remove_match_forecasts_but_blocks_props():
    report, _ = collect(datetime.now(UTC), wrong_roster=True)
    assert report["match_count"] == 3
    assert not any(report["props"].values())


def test_valid_no_bet_forecasts_stay_descriptive_and_do_not_become_recommendations():
    report, _ = collect(datetime.now(UTC), skip=True)
    assert report["match_count"] == 3
    assert all(row["action"] == "SKIP" for rows in report["matches"].values() for row in rows)
    assert "(no bet)" in digest.render(report)


def test_missing_score_data_is_not_replaced_by_a_fabricated_score():
    now = datetime.now(UTC)
    api, props, roster, _ = readers(now)
    def no_score(path, params):
        payload, status = api(path, params)
        return (payload, status) if "/dashboard/" in path else ({"error": "Score unavailable"}, 422)
    report = digest.collect(no_score, props, now=now, clock=lambda: now, leagues=[39], roster_loader=roster)
    assert report["match_count"] == 0


def test_expiry_during_collection_withdraws_the_forecast():
    now = datetime.now(UTC)
    api, props, roster, _ = readers(now)
    moments = [now] * 100
    def clock():
        return moments.pop() if moments else now + timedelta(hours=3)
    # Advancing from a fresh schedule to an expired forecast must not mail stale data.
    def expire_after_score(path, params):
        payload, code = api(path, params)
        if "/dashboard/" not in path:
            moments.clear()
        return payload, code
    report = digest.collect(expire_after_score, props, now=now, clock=clock, leagues=[39], roster_loader=roster)
    assert report["match_count"] == 0


def test_render_preserves_uncertainty_and_escapes_provider_text():
    report, _ = collect(datetime.now(UTC))
    report["matches"]["nba"][0]["side"] = '<script>alert("unsafe")</script>'
    report["props"]["nba"][0]["team"] = '<script>alert("unsafe")</script>'
    html = digest.render(report)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "OVER 3.5 Rebounds" in html and "model 62% vs book 45%" in html
    assert "not a proven edge" in html and "Historical target" not in html
    assert 1 <= html.count("vs book") <= 5  # one short line per pick, top five only


def test_side_is_chosen_at_the_book_line_not_the_players_median():
    now = datetime.now(UTC)
    # The model leaned under its own median (2.5) but at the book's 3.5 its 62% over beats the book's ~45%.
    best = digest._book_pick(priced_pick(now), now)
    assert (best["side"], best["line"]) == ("over", 3.5) and best["edge"] >= digest.MIN_EDGE
    # No real line, or a line the book prices about right, means no pick at all.
    assert digest._book_pick(priced_pick(now, quotes=False), now) is None
    assert digest._book_pick(priced_pick(now, over_odds=1.6, under_odds=2.45), now) is None
    stale = priced_pick(now);stale["sportsbook_quotes"][0]["expires_at"] = (now - timedelta(seconds=1)).isoformat()
    assert digest._book_pick(stale, now) is None


def test_only_the_top_games_of_each_sport_are_priced():
    report, calls = collect(datetime.now(UTC), count=7)
    for sport in digest.SPORTS:
        assert calls.count("props:" + sport) == digest.PRICED_GAMES[sport]
        assert len(report["props"][sport]) == 1


def test_soccer_props_use_their_own_roster_check_not_an_espn_lookup():
    now = datetime.now(UTC)
    api, props, _, _ = readers(now)
    def espn_404(sport, *args):
        if sport == "soccer":
            raise AssertionError("soccer slate ids are not ESPN ids")
        return [{"displayName": "Healthy Player", "status": {"type": "active"}}]
    report = digest.collect(api, props, now=now, clock=lambda: now, leagues=[39], roster_loader=espn_404,
                            injury_loader=lambda sport: ([], fresh(now)))
    assert report["props"]["soccer"] and not any("SOCCER props unavailable" in note for note in report["notes"])
    report, _ = collect(now, wrong_roster=True)  # unverified soccer roster: no soccer picks
    assert not report["props"]["soccer"] and any("roster not verified" in note for note in report["notes"])


def test_preview_is_unsent_and_does_not_mark_day_delivered():
    digest.configure("recipient@example.com")
    sender = Mock()
    result = digest.run(None, preview=True, collector=empty_report, sender=sender)
    assert result["state"] == "preview"
    assert (digest.root() / "latest-email.html").exists()
    assert not digest.receipt_path(digest.settings(), datetime.now(digest.ZONE).date().isoformat()).exists()
    sender.assert_not_called()


def test_one_delivery_per_day_survives_scheduler_reentry(monkeypatch):
    mail_ready(monkeypatch)
    calls = []
    def send(subject, html, recipient, before_submit):
        before_submit()
        calls.append(recipient)
        return "message-id"
    assert digest.run(None, collector=empty_report, sender=send)["state"] == "sent"
    assert digest.run(None, collector=Mock(side_effect=AssertionError("Must not re-collect")), sender=send)["state"] == "already_sent_or_needs_review"
    assert calls == ["recipient@example.com"]


def test_uncertain_submission_is_never_automatically_retried(monkeypatch):
    mail_ready(monkeypatch)
    calls = []
    def fail(subject, html, recipient, before_submit):
        before_submit()
        calls.append(1)
        raise OSError("private-test-secret must never be persisted")
    result = digest.run(None, collector=empty_report, sender=fail)
    assert result["state"] == "delivery_unknown"
    assert digest.run(None, collector=empty_report, sender=fail)["state"] == "already_sent_or_needs_review"
    assert len(calls) == 1
    content = digest.receipt_path(digest.settings(), datetime.now(digest.ZONE).date().isoformat()).read_text()
    assert "private-test-secret" not in content


def test_auth_failure_before_submission_can_be_safely_retried(monkeypatch):
    mail_ready(monkeypatch)
    def fail(*args):
        raise ValueError("Authentication not ready")
    assert digest.run(None, collector=empty_report, sender=fail)["state"] == "failed_before_submission"
    def success(subject, html, recipient, before_submit):
        before_submit()
        return "after-auth-fix"
    assert digest.run(None, collector=empty_report, sender=success)["state"] == "sent"


def test_parallel_runner_is_blocked_by_persistent_lock(monkeypatch):
    mail_ready(monkeypatch)
    digest.root().mkdir(parents=True, exist_ok=True)
    with FileLock(str(digest.root() / "delivery.lock"), timeout=0):
        assert digest.run(None, collector=empty_report)["state"] == "already_running"


def test_scheduler_registers_one_eastern_daily_job_only_when_opted_in(monkeypatch):
    from services import scheduler
    from services.pipeline_monitor import PipelineMonitor
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler.BackgroundScheduler, "start", lambda self: None)
    digest.configure("recipient@example.com", "13:00")
    sched = scheduler.start(SimpleNamespace(app_context=nullcontext), None, None, None, PipelineMonitor(), match_brain=None)
    try:
        job = sched.get_job("daily_sports_digest")
        assert job is not None
        assert str(job.trigger.timezone) == "America/New_York"
        assert "hour='13'" in str(job.trigger)
        assert job.max_instances == 1
        job.func()  # Missing SMTP credentials is a blocked status, not a send.
    finally:
        scheduler.stop()


def test_market_quote_uses_its_own_target_and_cannot_survive_expiry():
    now = datetime.now(UTC)
    pick = {"side": "over", "sportsbook_quotes": [{"side": "over", "line": 3.5, "model_chance": .6,
        "decimal_odds": 1.91, "bookmaker": "Controlled test book", "updated_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=10)).isoformat()}]}
    assert digest._prop_market(pick, now)["line"] == 3.5
    assert digest._prop_market(pick, now + timedelta(seconds=11)) is None
    pick["sportsbook_quotes"][0]["model_chance"] = None
    assert digest._prop_market(pick, now) is None


def test_bad_private_config_cannot_break_local_runtime_status():
    digest.root().mkdir(parents=True)
    (digest.root() / "config.json").write_text('{"time": "99:00"}')
    assert digest.status()["delivery"] == "blocked_invalid_configuration"


@pytest.mark.parametrize("hour", [6, 13])
def test_eastern_cron_keeps_requested_hour_through_daylight_saving_change(hour):
    from apscheduler.triggers.cron import CronTrigger
    trigger = CronTrigger(hour=hour, minute=0, timezone=digest.ZONE)
    summer = trigger.get_next_fire_time(None, datetime(2026, 10, 31, 10, tzinfo=UTC))
    winter = trigger.get_next_fire_time(None, datetime(2026, 11, 2, 10, tzinfo=UTC))
    assert summer.hour == winter.hour == hour
    assert summer.astimezone(UTC).hour == hour + 4 and winter.astimezone(UTC).hour == hour + 5


def test_expired_api_sources_must_be_renewed_by_real_api_reads():
    now = datetime.now(UTC)
    current = [now]
    base_api, props, roster, _ = readers(now)
    def api(path, params):
        payload, code = base_api(path, params)
        metadata = {"state": "fresh", "observed_at": current[0].isoformat(),
                    "expires_at": (current[0] + timedelta(seconds=2)).isoformat()}
        if "slate" in payload:
            for card in payload["slate"]:
                card["dataFreshness"] = metadata
        else:
            payload["result"]["data_freshness"] = metadata
        return payload, code
    def slow_props(*args):
        payload, code = props(*args)
        current[0] += timedelta(seconds=5)
        return payload, code
    report = digest.collect(api, slow_props, now=now, clock=lambda: current[0], leagues=[39], roster_loader=roster,
                            injury_loader=lambda sport: ([], fresh(current[0])))
    assert report["match_count"] == 3
    assert all(row["data_freshness"]["observed_at"] == current[0].isoformat()
               for rows in report["matches"].values() for row in rows)


@pytest.mark.parametrize("renewal", ["fresh", "failed", "withdrawn"])
def test_slow_other_sport_schedule_requires_new_original_source_read(renewal):
    # Keep the fixture and requested partition on the same Eastern calendar day.
    # A wall-clock evening run moves the +5-hour fixture into tomorrow.
    now = datetime(2026, 10, 24, 12, tzinfo=UTC)
    current, nba_reads = [now], []
    base_api, props, roster, _ = readers(now)

    def api(path, params):
        if "/dashboard/" in path:
            if params["date"] != digest.calendar_date(now):
                return {"slate": []}, 200
            if path.endswith("/soccer"):
                current[0] = now + timedelta(minutes=2)
            if path.endswith("/nba"):
                nba_reads.append(1)
                if len(nba_reads) > 1 and renewal != "fresh":
                    return {"slate": []}, 503 if renewal == "failed" else 200
        payload, code = base_api(path, params)
        metadata = {"state": "fresh", "observed_at": current[0].isoformat(),
                    "expires_at": (current[0] + timedelta(minutes=1)).isoformat()}
        if "slate" in payload:
            for card in payload["slate"]:
                card["dataFreshness"] = metadata
        else:
            payload["result"]["data_freshness"] = metadata
        return payload, code

    report = digest.collect(api, props, now=now, clock=lambda: current[0], leagues=[39], roster_loader=roster,
                            injury_loader=lambda sport: ([], fresh(current[0])))
    assert len(nba_reads) == 2
    assert len(report["matches"]["nba"]) == (1 if renewal == "fresh" else 0)
    if renewal == "fresh":
        assert report["matches"]["nba"][0]["data_freshness"]["observed_at"] == current[0].isoformat()
    else:
        assert not report["props"]["nba"]
