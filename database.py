"""SQLite database — conversations, messages, contacts."""

from __future__ import annotations

import datetime
from pathlib import Path

from sqlalchemy import (
    Column,
    String,
    Integer,
    Text,
    DateTime,
    Float,
    Boolean,
    JSON,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session as SASession

from config import get_settings

settings = get_settings()

# Ensure data dir exists
settings.db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(f"sqlite:///{settings.db_path}", echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class Contact(Base):
    """A business contact / lead."""

    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True)
    phone_number = Column(String(20), unique=True, nullable=False, index=True)
    first_name = Column(String(100), default="")
    last_name = Column(String(100), default="")
    business_name = Column(String(200), default="")
    email = Column(String(200), default="")  # for email fallback (Review Engine)
    tags = Column(JSON, default=list)  # ["missed-call", "hot-lead", "unqualified", "reviewed", "opted-out"]
    source = Column(String(50), default="missed-call")  # missed-call, scrape
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class Conversation(Base):
    """A full AI-customer conversation thread."""

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True)
    contact_id = Column(Integer, index=True, nullable=True)
    phone_number = Column(String(20), nullable=False, index=True)
    business_type = Column(String(50), default="default")
    state = Column(String(20), default="active")  # active, completed, timed_out, taken_over
    branch = Column(String(30), default=None)  # booked, emergency, unqualified, hot_lead
    initial_delay_seconds = Column(Integer, default=60)
    max_responses = Column(Integer, default=5)
    response_count = Column(Integer, default=0)
    last_ai_sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    metadata_json = Column(JSON, default=dict)


class Message(Base):
    """Individual SMS exchange."""

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, index=True, nullable=False)
    role = Column(String(10), nullable=False)  # user, assistant, system
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class WhitelistEntry(Base):
    """Known contacts that should bypass the AI text-back."""

    __tablename__ = "whitelist"

    id = Column(Integer, primary_key=True)
    phone_number = Column(String(20), nullable=False, index=True)
    name = Column(String(100), default="")
    added_at = Column(DateTime, default=datetime.datetime.utcnow)


class ScheduledTask(Base):
    """A scheduled outbound SMS — reminder, follow-up, or review request."""

    __tablename__ = "scheduled_tasks"

    id = Column(Integer, primary_key=True)
    kind = Column(String(30), nullable=False, index=True)  # reminder, follow_up, review, thank_you, review_follow_up, review_email
    phone_number = Column(String(20), nullable=False, index=True)
    conversation_id = Column(Integer, nullable=True, index=True)
    run_at = Column(DateTime, nullable=False, index=True)  # UTC
    body = Column(Text, nullable=False)
    status = Column(String(20), default="pending", index=True)  # pending, sent, cancelled, failed
    attempts = Column(Integer, default=0)
    payload = Column(JSON, default=dict)  # extras: customer_name, service_type, review_link, email
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)


def init_db():
    Base.metadata.create_all(engine)
    _migrate_legacy_columns()


def _migrate_legacy_columns():
    """Add columns that didn't exist in older DB files (SQLite ALTER)."""
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        contact_cols = {c["name"] for c in inspector.get_columns("contacts")}
        task_cols = {c["name"] for c in inspector.get_columns("scheduled_tasks")}
        message_cols = {c["name"] for c in inspector.get_columns("messages")}
    except Exception:
        return  # tables may not exist yet on very first run — create_all handles it

    db = get_session()
    try:
        if "email" not in contact_cols:
            db.execute(text("ALTER TABLE contacts ADD COLUMN email VARCHAR(200) DEFAULT ''"))
        if "payload" not in task_cols:
            db.execute(text("ALTER TABLE scheduled_tasks ADD COLUMN payload JSON DEFAULT '{}'"))
        # Twilio scrub (Aug 10): drop the legacy twilio_sid column if present
        if "twilio_sid" in message_cols:
            db.execute(text("ALTER TABLE messages DROP COLUMN twilio_sid"))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[database] migration warning: {e}")
    finally:
        db.close()


def get_session() -> SASession:
    return SessionLocal()
