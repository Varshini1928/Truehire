"""Relational storage. Timestamps are UTC; browser renders them locally."""

from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(value):
    return value.isoformat() + "Z" if value else None


@event.listens_for(Engine, "connect")
def sqlite_pragmas(connection, _):
    if type(connection).__module__ == "sqlite3":
        cur = connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(10), nullable=False, default="user")
    token_version = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    __table_args__ = (db.CheckConstraint("role IN ('user','admin')"),)


class ScanHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    text = db.Column(db.Text, nullable=False, default="")
    url = db.Column(db.Text, nullable=False, default="")
    domain = db.Column(db.String(253), default="")
    verdict = db.Column(db.String(10), nullable=False, index=True)
    confidence = db.Column(db.Float, nullable=True)
    risk_score = db.Column(db.Float, nullable=True)
    override = db.Column(db.Boolean, default=False)
    reasons = db.Column(db.JSON, default=list)
    source = db.Column(db.String(60), nullable=False)
    model_version = db.Column(db.String(40), nullable=False)
    duration_ms = db.Column(db.Float, nullable=False)
    text_ms = db.Column(db.Float, nullable=True)
    image_ms = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    def public(self):
        return {
            k: getattr(self, k)
            for k in (
                "id",
                "text",
                "url",
                "domain",
                "verdict",
                "confidence",
                "risk_score",
                "override",
                "reasons",
                "source",
                "model_version",
                "duration_ms",
            )
        } | {"created_at": iso(self.created_at)}


class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    scan_id = db.Column(
        db.Integer,
        db.ForeignKey("scan_history.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label = db.Column(db.String(4), nullable=False)
    trained = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    __table_args__ = (db.CheckConstraint("label IN ('real','fake')"),)


class Job(db.Model):
    """Durable status records; execution itself uses a bounded in-process pool."""

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = db.Column(db.String(8), nullable=False)
    status = db.Column(db.String(12), nullable=False, default="queued")
    logs = db.Column(db.JSON, default=list)
    result = db.Column(db.JSON, nullable=True)
    error = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(60), nullable=False)
    target_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
