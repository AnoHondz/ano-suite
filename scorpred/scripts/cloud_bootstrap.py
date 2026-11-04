"""Restore an initial private account snapshot from service secrets, never Git."""
import base64
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

from filelock import FileLock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_paths import auth_db_path, persistent_root


def restore_account_snapshot(encoded=None, checksum=None, target=None):
    encoded = encoded if encoded is not None else os.getenv("SCORPRED_AUTH_BOOTSTRAP_B64", "")
    checksum = checksum if checksum is not None else os.getenv("SCORPRED_AUTH_BOOTSTRAP_SHA256", "")
    if not encoded:
        return "not_requested"
    target = Path(target or auth_db_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(target) + ".restore.lock", timeout=10):
        if target.exists():
            return "already_exists"
        if len(encoded) > 12_000_000:
            raise ValueError("Account snapshot exceeds size limit")
        data = base64.b64decode(encoded, validate=True)
        if not checksum or hashlib.sha256(data).hexdigest() != checksum:
            raise ValueError("Account snapshot checksum mismatch")
        if not data.startswith(b"SQLite format 3\0"):
            raise ValueError("Account snapshot is not a SQLite database")
        handle, name = tempfile.mkstemp(dir=target.parent, suffix=".sqlite3")
        temporary = Path(name)
        try:
            with os.fdopen(handle, "wb") as output:
                output.write(data)
            with closing(sqlite3.connect(temporary)) as db:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("Account snapshot integrity check failed")
            # Do not replace a database, even if another startup created one.
            os.link(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return "restored"


def verify_cloud_email(*, sender=None, journal=None):
    from services import daily_digest
    path = Path(journal or persistent_root() / "notifications" / "cloud-email-check.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=10):
        previous = json.loads(path.read_text()) if path.exists() else {}
        if previous.get("state") in {"sent", "submitting", "delivery_unknown"}:
            return previous["state"]
        recipient = os.getenv("DAILY_DIGEST_RECIPIENT", "").strip()
        if not recipient or daily_digest.status()["delivery"] != "ready":
            return "blocked_missing_credentials"
        receipt = {"state": "prepared"}
        def before_submit():
            receipt["state"] = "submitting"
            daily_digest._save(path, receipt)
        try:
            message_id = (sender or daily_digest._send)("ScorPred cloud email connection test",
                "<p>ScorPred is running on Railway and this message verifies cloud email delivery. "
                "The daily schedule is still disabled during deployment verification.</p>", recipient, before_submit)
            receipt.update(state="sent", message_id=message_id)
        except Exception as exc:
            receipt.update(state="delivery_unknown" if receipt["state"] == "submitting" else "failed_before_submission",
                           error_type=type(exc).__name__)
        daily_digest._save(path, receipt)
        return receipt["state"]


if __name__ == "__main__":
    print("Account bootstrap:", restore_account_snapshot())
    if os.getenv("SCORPRED_CLOUD_EMAIL_VERIFY", "0") == "1":
        print("Cloud email check:", verify_cloud_email())
