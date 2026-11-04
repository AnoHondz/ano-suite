import base64
import hashlib
import sqlite3

import pytest

from scripts.cloud_bootstrap import restore_account_snapshot, verify_cloud_email


def test_account_restore_preserves_existing_database(tmp_path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE example (id INTEGER)")
        db.execute("INSERT INTO example VALUES (7)")
    raw = source.read_bytes()
    target = tmp_path / "cloud" / "auth.db"
    assert restore_account_snapshot(base64.b64encode(raw).decode(), hashlib.sha256(raw).hexdigest(), target) == "restored"
    assert target.read_bytes() == raw
    assert restore_account_snapshot("invalid", "invalid", target) == "already_exists"
    assert target.read_bytes() == raw


def test_invalid_account_snapshot_does_not_create_database(tmp_path):
    target = tmp_path / "auth.db"
    with pytest.raises(ValueError):
        restore_account_snapshot(base64.b64encode(b"not a database").decode(), "wrong", target)
    assert not target.exists()


def test_cloud_email_check_sends_once_and_never_retries_ambiguous_submission(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_USER", "owner@example.com")
    monkeypatch.setenv("SMTP_PASS", "test-password")
    monkeypatch.setenv("DAILY_DIGEST_RECIPIENT", "owner@example.com")
    journal = tmp_path / "email.json"
    called = []
    def sender(subject, html, recipient, before_submit):
        called.append(recipient)
        before_submit()
        return "test-id"
    assert verify_cloud_email(sender=sender, journal=journal) == "sent"
    assert verify_cloud_email(sender=sender, journal=journal) == "sent"
    assert called == ["owner@example.com"]
    journal = tmp_path / "ambiguous.json"
    def uncertain(subject, html, recipient, before_submit):
        before_submit()
        raise ConnectionError
    assert verify_cloud_email(sender=uncertain, journal=journal) == "delivery_unknown"
    assert verify_cloud_email(sender=sender, journal=journal) == "delivery_unknown"
    assert len(called) == 1
