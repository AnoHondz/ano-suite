from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from services import props_email as mail


@pytest.fixture(autouse=True)
def private_mail(monkeypatch, tmp_path):
    monkeypatch.setattr(mail, "root", lambda: tmp_path)
    monkeypatch.setenv("SMTP_USER", "owner@example.com")
    monkeypatch.setenv("SMTP_PASS", "test-secret")
    mail._active.clear()
    yield
    mail._active.clear()


def report():
    return {"date": "2026-10-25", "sports": ["nfl"], "generated_at": datetime.now(UTC).isoformat(),
            "pick_count": 1, "unavailable": [], "games": [{"sport": "nfl", "matchup": "A vs B",
            "kickoff": (datetime.now(UTC) + timedelta(hours=3)).isoformat(), "picks": [
                {"name": "<Player>", "team": "A", "side": "over", "line": 2.5, "label": "Receptions",
                 "chance": .65, "line_source": "historical"}]}]}


def test_missing_credentials_never_starts_collection(monkeypatch):
    monkeypatch.delenv("SMTP_PASS")
    submit = Mock()
    assert mail.start("1", "owner@example.com", "2026-10-25", ["nfl"], report, submit=submit)["state"] == "blocked"
    submit.assert_not_called()


def test_duplicate_running_sent_and_uncertain_jobs_are_not_resent():
    submit = Mock()
    job = mail.start("owner@example.com", "owner@example.com", "2026-10-25", ["nfl"], report, submit=submit)
    assert mail.start("owner@example.com", "owner@example.com", "2026-10-25", ["nfl"], report, submit=submit)["id"] == job["id"]
    assert submit.call_count == 1
    sent = []
    def sender(subject, html, recipient, before_submit):
        assert '&lt;Player&gt;' in html
        before_submit()
        sent.append(recipient)
        return "message-id"
    mail.execute(job, report, sender=sender)
    assert mail.read(job["id"], job["owner"])["state"] == "sent"
    mail.start(job["owner"], job["recipient"], job["date"], job["sports"], report, submit=submit)
    assert submit.call_count == 1 and len(sent) == 1


def test_submission_failure_is_uncertain_and_blocks_retries():
    job = mail.start("1", "owner@example.com", "2026-10-25", ["nfl"], report, submit=Mock())
    def sender(subject, html, recipient, before_submit):
        before_submit()
        raise ConnectionError("SMTP disconnected after DATA")
    mail.execute(job, report, sender=sender)
    assert mail.read(job["id"], "1")["state"] == "delivery_unknown"
    submit = Mock()
    mail.start("1", job["recipient"], job["date"], job["sports"], report, submit=submit)
    submit.assert_not_called()


def test_collect_retries_freshness_failure_and_reports_missing_games():
    now = datetime.now(UTC)
    kickoff = (now + timedelta(hours=3)).isoformat()
    cards = [{"matchId": str(i), "matchup": f"Game {i}", "matchDate": kickoff,
              "pregameAvailable": True, "eventStatus": "pre"} for i in range(2)]
    def api(path, params):
        return {"slate": cards}, 200
    counts = {"0": 0, "1": 0}
    def props(sport, card, day):
        key = card["matchId"]
        counts[key] += 1
        if key == "1" or counts[key] == 1:
            return {"error": "Source needs refresh"}, 422
        return {"result": {"picks": report()["games"][0]["picks"]}}, 200
    result = mail.collect(mail.calendar_date(kickoff), ["nfl"], api, props, clock=lambda: now, sleep=lambda _: None)
    assert result["pick_count"] == 1
    assert counts == {"0": 2, "1": 2}
    assert result["unavailable"][0]["matchup"] == "Game 1"


def test_status_is_owner_scoped_and_api_ignores_supplied_recipient(monkeypatch):
    import app
    monkeypatch.setattr(app.user_auth, "current_user", lambda: {"id": 7, "email": "owner@example.com"})
    start = Mock(return_value={"state": "collecting", "id": "a" * 64})
    monkeypatch.setattr(mail, "start", start)
    day = datetime.now(mail.daily_digest.ZONE).date().isoformat()
    with app.app.test_client() as client:
        response = client.post('/api/props/email', json={"date": day, "sports": ["nfl"], "recipient": "other@example.com"})
        assert response.status_code == 202
        assert start.call_args.args[:2] == ("owner@example.com", "owner@example.com")
        assert client.get('/api/props/email?id=' + 'b' * 64).status_code == 404


def test_api_requires_sign_in_and_csrf(monkeypatch):
    import app
    monkeypatch.setattr(app.user_auth, "current_user", lambda: None)
    with app.app.test_client() as client:
        assert client.get('/api/props/email').status_code == 401
        monkeypatch.setitem(app.app.config, "WTF_CSRF_ENABLED", True)
        assert client.post('/api/props/email', json={}).status_code == 400
