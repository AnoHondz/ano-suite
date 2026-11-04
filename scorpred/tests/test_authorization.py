from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, text

import app as module
import user_auth
from db_models import Bet, BetSubmission, db, migrate_schema
from services import bets_service


@pytest.fixture
def users():
    with module.app.app_context():
        for email in ("alice@example.test", "bob@example.test"):
            user_auth._save_user({"email": email, "password_hash": user_auth._hash_password("test-password")})
        return {name: user_auth._load_user(f"{name}@example.test")["id"] for name in ("alice", "bob")}


def client_for(name=None):
    client = module.app.test_client()
    if name:
        with client.session_transaction() as sess:
            sess[user_auth.USER_SESSION_KEY] = f"{name}@example.test"
    return client


def payload():
    return {"match_id": "123", "matchup": "Home vs Away", "action": "CONSIDER",
            "recommended_side": "Home", "confidence": 65}


def test_anonymous_cannot_create_list_or_clear_bets(users):
    client = client_for()
    for method, path in (("post", "/api/bets"), ("get", "/api/bets"), ("delete", "/api/bets"), ("delete", "/api/bets/1")):
        response = getattr(client, method)(path, json=payload())
        assert response.status_code == 401
    with module.app.app_context():
        assert Bet.query.count() == 0


def test_owner_is_server_assigned_and_other_users_cannot_access_record(users):
    alice, bob = client_for("alice"), client_for("bob")
    data = payload() | {"user_id": users["bob"]}
    saved = alice.post("/api/bets", json=data)
    assert saved.status_code == 200
    bet_id = saved.json["bet"]["id"]
    assert bob.get("/api/bets").json == {"bets": []}
    assert bob.delete(f"/api/bets/{bet_id}").status_code == 404
    assert bob.delete("/api/bets").status_code == 200
    assert len(alice.get("/api/bets").json["bets"]) == 1
    assert alice.delete(f"/api/bets/{bet_id}").status_code == 200


def test_dedupe_is_owner_scoped_and_expires(users):
    with module.app.app_context():
        first = bets_service.create_bet(payload(), user_id=users["alice"])
        with pytest.raises(bets_service.BetValidationError, match="duplicate"):
            bets_service.create_bet(payload(), user_id=users["alice"])
        bets_service.create_bet(payload(), user_id=users["bob"])
        BetSubmission.query.update({"last_created_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=31)})
        db.session.commit()
        second = bets_service.create_bet(payload(), user_id=users["alice"])
        assert second["id"] != first["id"]


def test_mutations_require_csrf_even_for_authenticated_owner(users, monkeypatch):
    monkeypatch.setitem(module.app.config, "WTF_CSRF_ENABLED", True)
    client = client_for("alice")
    assert client.post("/api/bets", json=payload()).status_code == 400
    token = client.get("/api/session").json["csrf_token"]
    assert client.post("/api/bets", json=payload(), headers={"X-CSRF-Token": token}).status_code == 200


def test_legacy_mutation_still_reaches_authorization_with_spa_build(users, monkeypatch, tmp_path):
    index = tmp_path / "index.html"
    index.write_text("SPA", encoding="utf-8")
    monkeypatch.setattr(module, "_SPA_INDEX", index)
    monkeypatch.setitem(module.app.config, "SERVE_SPA", True)
    assert client_for().post("/clear-bets").status_code == 401


def test_existing_shared_rows_are_quarantined_during_migration(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "legacy.db"))
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE "user" (id INTEGER PRIMARY KEY, email TEXT)'))
        conn.execute(text('INSERT INTO "user" VALUES (1, \'old@example.test\')'))
        conn.execute(text("CREATE TABLE bet (id INTEGER PRIMARY KEY, matchup TEXT)"))
        conn.execute(text("INSERT INTO bet VALUES (1, 'Legacy')"))
    migrate_schema(engine)
    migrate_schema(engine)
    assert "user_id" in {c["name"] for c in inspect(engine).get_columns("bet")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT user_id FROM bet WHERE id=1")).scalar() is None
        assert not conn.execute(text('SELECT is_admin FROM "user" WHERE id=1')).scalar()
    engine.dispose()


def test_admin_refresh_rejects_regular_users(users):
    assert client_for("alice").post("/admin/refresh-cache").status_code == 403


def test_only_explicitly_promoted_admin_can_refresh(users, monkeypatch):
    from db_models import User
    with module.app.app_context():
        user = db.session.get(User, users["alice"])
        user.is_admin = True
        db.session.commit()
    monkeypatch.setattr(module, "_refresh_soccer_cache", lambda _: {"refreshed": True})
    assert client_for("alice").post("/admin/refresh-cache").status_code == 200


def test_user_data_cannot_grant_administrator_role():
    with module.app.app_context():
        user_auth._save_user({"email": "claimed@example.test", "password_hash": user_auth._hash_password("test-password"), "is_admin": True})
        assert user_auth._load_user("claimed@example.test")["is_admin"] is False


def test_login_rate_limit_rejects_sixth_post(users, monkeypatch):
    monkeypatch.setattr(module.limiter, "enabled", True)
    module.limiter.reset()
    client = client_for()
    statuses = [client.post("/login", data={"email": "bad@example.test", "password": "bad"}).status_code for _ in range(6)]
    assert statuses[:5] == [200] * 5
    assert statuses[5] == 429


def test_api_rate_limit_cannot_be_bypassed_with_forwarded_header(monkeypatch):
    monkeypatch.setattr(module.limiter, "enabled", True)
    module.limiter.reset()
    monkeypatch.setattr(module._scoreline_engine, "predict_soccer_scoreline", lambda **kw: {})
    client = client_for()
    statuses = [client.get("/api/scoreline/soccer", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(31)]
    assert statuses[:30] == [400] * 30  # Invalid requests are still rate-limited.
    assert statuses[-1] == 429


def test_legacy_password_hash_verifies_and_upgrades_on_login():
    email = "legacy@example.test"
    legacy = user_auth._legacy_hash_password("old-password", "abcd1234")
    with module.app.app_context():
        user_auth._save_user({"email": email, "password_hash": legacy})
        assert user_auth._verify_password("old-password", legacy)
        assert not user_auth._verify_password("wrong-password", legacy)
        user_auth._upgrade_legacy_hash(email, "old-password", legacy)
        upgraded = user_auth._load_user(email, include_password_hash=True)["password_hash"]
    assert upgraded != legacy
    assert not user_auth._is_legacy_hash(upgraded)
    assert user_auth._verify_password("old-password", upgraded)


def test_wrong_method_returns_405_not_500():
    response = module.app.test_client().get("/admin/refresh-cache", headers={"Accept": "application/json"})
    assert response.status_code == 405
    assert response.get_json()["code"] == 405
