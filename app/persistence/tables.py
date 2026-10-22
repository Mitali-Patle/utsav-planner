"""Database tables. Portable types, so the same code runs on Neon Postgres and SQLite (tests)."""

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, MetaData, String, Table

metadata = MetaData()

events = Table(
    "events",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("owner", String(64), nullable=False, index=True, comment="SHA-256 of the session id"),
    Column("version", Integer, nullable=False),
    Column("plan", JSON, nullable=False),
    Column("ai", JSON, nullable=True, comment="Personalization result and provenance"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

proposals = Table(
    "proposals",
    metadata,
    Column("id", String(32), primary_key=True),
    Column(
        "event_id",
        String(32),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("base_version", Integer, nullable=False),
    Column("request_text", String(300), nullable=False),
    Column("intent", JSON, nullable=False, comment="AI interpretation and provenance"),
    Column("proposal", JSON, nullable=False),
    Column("status", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("decided_at", DateTime(timezone=True), nullable=True),
)

ai_cache = Table(
    "ai_cache",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", JSON, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
)

rate_limits = Table(
    "rate_limits",
    metadata,
    Column("bucket", String(80), primary_key=True),
    Column("window_start", Integer, primary_key=True),
    Column("count", Integer, nullable=False),
)
