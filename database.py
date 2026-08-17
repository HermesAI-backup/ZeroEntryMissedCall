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
    event,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session as SASession

from config import get_settings

settings = get_settings()

# Ensure data dir exists
settings.db_path.parent.mkdir(parents=True, exist_ok=True)

# Multi-client concurrency: multiple conversations write at once (each inbound
# SMS is an independent async task). WAL lets readers/writers overlap, the
# busy timeout stops "database is locked" under concurrent writes.
engine = create_engine(
    f"sqlite:///{settings.db_path}",
    echo=False,
    connect_args={"timeout": 15},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()
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


class Client(Base):
    """A Zero Entry client business. Everything per-client lives here —
    identity, sender number, owner, calendar/sheet, Telnyx profile/campaign.

    The first client is Zero Entry's own dogfood (sales persona, 406 number).
    """

    __tablename__ = "clients"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, unique=True)
    # Persona + identity render context (replaces the global .env values)
    business_type = Column(String(50), default="default")
    business_name = Column(String(200), default="")
    service_area = Column(String(200), default="")
    review_link = Column(String(500), default="")
    # Outbound sender + alert routing
    telnyx_number = Column(String(20), default="")       # their leased number (outbound from)
    owner_phone = Column(String(20), default="")         # emergency + booking alerts
    # Scheduling: per-client calendar + sheet (defaults = current single ones)
    calendar_id = Column(String(200), default="")        # empty = default calendar
    spreadsheet_id = Column(String(200), default="")     # empty = default spreadsheet
    # Telnyx infra ids
    messaging_profile_id = Column(String(100), default="")
    texml_app_id = Column(String(100), default="")
    campaign_id = Column(String(100), default="")        # 10DLC campaign (per-business, required)
    business_hours = Column(String(200), default="")     # e.g. "Mon-Fri 8-5" (pending wiring)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class Conversation(Base):
    """A full AI-customer conversation thread."""

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True)
    contact_id = Column(Integer, index=True, nullable=True)
    client_id = Column(Integer, index=True, nullable=True)  # multi-client (Step 1)
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
    ensure_default_client()


def ensure_default_client() -> int:
    """Upsert the dogfood/default client from current .env settings.

    Until real clients are onboarded this record mirrors .env, so flipping
    BUSINESS_TYPE / BUSINESS_NAME / SERVICE_AREA re-syncs it on startup.
    Real clients get static rows (see the multi-client build, MTL §3).
    Returns the client id.
    """
    db = get_session()
    try:
        values = dict(
            business_type=settings.business_type,
            business_name=settings.business_name,
            service_area=settings.service_area,
            review_link=settings.review_link,
            telnyx_number=settings.telnyx_from,
            owner_phone=settings.business_owner_phone,
            campaign_id="CW1QZJ1",
            active=True,
        )
        client = db.query(Client).filter(Client.name == "Default (env mirror)").first()
        if client:
            for k, v in values.items():
                setattr(client, k, v)
        else:
            client = Client(name="Default (env mirror)", **values)
            db.add(client)
        db.commit()
        return client.id
    finally:
        db.close()


def _migrate_legacy_columns():
    """Add columns that didn't exist in older DB files (SQLite ALTER)."""
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        contact_cols = {c["name"] for c in inspector.get_columns("contacts")}
        task_cols = {c["name"] for c in inspector.get_columns("scheduled_tasks")}
        message_cols = {c["name"] for c in inspector.get_columns("messages")}
        conv_cols = {c["name"] for c in inspector.get_columns("conversations")}
    except Exception:
        return  # tables may not exist yet on very first run — create_all handles it

    db = get_session()
    try:
        if "email" not in contact_cols:
            db.execute(text("ALTER TABLE contacts ADD COLUMN email VARCHAR(200) DEFAULT ''"))
        if "payload" not in task_cols:
            db.execute(text("ALTER TABLE scheduled_tasks ADD COLUMN payload JSON DEFAULT '{}'"))
        if "client_id" not in conv_cols:
            db.execute(text("ALTER TABLE conversations ADD COLUMN client_id INTEGER"))
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
