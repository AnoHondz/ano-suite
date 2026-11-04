from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
from sqlalchemy import UniqueConstraint, false

db = SQLAlchemy()

# Enforce ownership foreign keys and allow short concurrent SQLite transactions.
from sqlalchemy import event
from sqlalchemy.engine import Engine
import sqlite3


@event.listens_for(Engine, "connect")
def configure_sqlite(connection, _record):
    if isinstance(connection, sqlite3.Connection):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False)
    saved_picks = db.Column(db.JSON, default=list)
    history = db.Column(db.JSON, default=list)
    is_admin = db.Column(db.Boolean, nullable=False, default=False, server_default=false())

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "created_at": self.created_at.isoformat() + "Z",
            "saved_picks": self.saved_picks or [],
            "history": self.history or [],
            "is_admin": bool(self.is_admin),
        }


class Bet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Nullable only to quarantine pre-ownership records during additive migration.
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    match_id = db.Column(db.String(80), nullable=True, index=True)
    matchup = db.Column(db.String(255), nullable=True)
    recommended_side = db.Column(db.String(120), nullable=True)
    action = db.Column(db.String(40), nullable=True)
    confidence = db.Column(db.Float, nullable=True)
    probabilities_json = db.Column(db.Text, nullable=True)
    data_quality = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    idempotency_key = db.Column(db.String(64), nullable=False, unique=True, index=True)

    __table_args__ = (
        UniqueConstraint("match_id", "idempotency_key", name="uq_bet_match_id_idempotency_key"),
    )


class BetSubmission(db.Model):
    """Atomic, owner-scoped lease for the 30-second submission window."""
    key = db.Column(db.String(64), primary_key=True)
    last_created_at = db.Column(db.DateTime, nullable=False)


def migrate_schema(engine):
    """Add ownership without assigning or deleting legacy shared records."""
    from sqlalchemy import inspect, text
    if "user" in inspect(engine).get_table_names():
        user_columns = {column["name"] for column in inspect(engine).get_columns("user")}
        if "is_admin" not in user_columns:
            with engine.begin() as connection:
                connection.execute(text('ALTER TABLE "user" ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE'))
    columns = {column["name"] for column in inspect(engine).get_columns("bet")}
    if "user_id" not in columns:
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE bet ADD COLUMN user_id INTEGER REFERENCES "user"(id)'))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_bet_user_id ON bet (user_id)"))
