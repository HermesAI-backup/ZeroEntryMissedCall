"""Regression tests: client_roi_report, review_queue, export_conversations,
and the multi-touch follow-up upgrade (automation.schedule_follow_up).

All four read the SQLite DB directly (no ORM imports from database.py — the
tools use raw sqlite3 to avoid the config/load_dotenv path), so the fixture
builds an ISOLATED temp DB via the same CREATE TABLE schema. The follow-up
test drives the real automation.py against a temp DB (env DB_PATH set before
import — config lru_cache reads it at first get_settings()).

Usage: python test_roi_review_export.py   (plain asserts, exit 0/1)
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))

SCHEMA = """
CREATE TABLE clients (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE, business_type TEXT,
    business_name TEXT, service_area TEXT, review_link TEXT, telnyx_number TEXT,
    owner_phone TEXT, calendar_id TEXT, spreadsheet_id TEXT,
    messaging_profile_id TEXT, texml_app_id TEXT, campaign_id TEXT,
    business_hours TEXT, active INTEGER DEFAULT 1,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY, contact_id INTEGER, client_id INTEGER,
    phone_number TEXT NOT NULL, business_type TEXT DEFAULT 'default',
    state TEXT DEFAULT 'active', branch TEXT, initial_delay_seconds INTEGER,
    max_responses INTEGER DEFAULT 5, response_count INTEGER DEFAULT 0,
    last_ai_sent_at TEXT, created_at TEXT, updated_at TEXT, metadata_json TEXT
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT
);
CREATE TABLE scheduled_tasks (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, phone_number TEXT NOT NULL,
    conversation_id INTEGER, run_at TEXT NOT NULL, body TEXT NOT NULL,
    status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,
    payload TEXT, created_at TEXT, sent_at TEXT
);
"""


def make_db() -> tuple[sqlite3.Connection, Path]:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="hermes-test-")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn, Path(path)


def seed_client(conn: sqlite3.Connection, name: str, btype: str) -> int:
    cur = conn.execute(
        "INSERT INTO clients (name, business_type, business_name, active) "
        "VALUES (?,?,?,1)", (name, btype, name))
    return cur.lastrowid


def seed_conv(conn: sqlite3.Connection, phone: str, client_id: int | None,
              btype: str, state: str = "active", branch: str | None = None,
              response_count: int = 0, max_responses: int = 10,
              created: str | None = None, meta: dict | None = None,
              sys_msg: bool = False, user_msgs: int = 0,
              ai_msgs: int = 0) -> int:
    created = created or "2026-08-18 10:00:00"
    cur = conn.execute(
        "INSERT INTO conversations (client_id, phone_number, business_type, "
        "state, branch, max_responses, response_count, created_at, metadata_json) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (client_id, phone, btype, state, branch, max_responses,
         response_count, created, json.dumps(meta or {})))
    cid = cur.lastrowid
    if sys_msg:
        conn.execute("INSERT INTO messages (conversation_id, role, content, created_at) "
                     "VALUES (?,?,?,?)", (cid, "system", f"Missed call from {phone}", created))
    for i in range(ai_msgs):
        conn.execute("INSERT INTO messages (conversation_id, role, content, created_at) "
                     "VALUES (?,?,?,?)", (cid, "assistant", f"AI reply {i}", created))
    for i in range(user_msgs):
        conn.execute("INSERT INTO messages (conversation_id, role, content, created_at) "
                     "VALUES (?,?,?,?)", (cid, "user", f"customer msg {i}", created))
    conn.commit()
    return cid


FAILS: list[str] = []


def check(name: str, cond: bool) -> None:
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------------------
# 1. client_roi_report funnel
# ---------------------------------------------------------------------------
def test_roi_funnel() -> None:
    import client_roi_report as roi

    conn, path = make_db()
    acme = seed_client(conn, "Acme Plumbing", "plumbing")
    # booked conv: missed call -> texted -> replied -> booked
    seed_conv(conn, "+14065551111", acme, "plumbing", branch="booked",
              response_count=3, meta={"_booked_event": "evt_1", "customer_name": "Tom"},
              sys_msg=True, user_msgs=2)
    # quiet conv: missed call -> texted, never replied
    seed_conv(conn, "+14065552222", acme, "plumbing", response_count=1,
              sys_msg=True, user_msgs=0)
    # direct text-in (no missed-call system msg), replied, not booked
    seed_conv(conn, "+14065553333", acme, "plumbing", response_count=2,
              sys_msg=False, user_msgs=1)
    # legacy conv with NO client_id -> groups under business_type
    seed_conv(conn, "+14065554444", None, "plumbing", response_count=1,
              sys_msg=True, user_msgs=0)
    # old conv outside window
    seed_conv(conn, "+14065555555", acme, "plumbing", response_count=1,
              created="2026-01-01 10:00:00", sys_msg=True, user_msgs=0)
    conn.commit()

    since = datetime.datetime(2026, 8, 1, 0, 0, 0)
    funnel = roi.build_funnel(conn, since)
    check("roi: 2 client groups", len(funnel) == 2)
    acme_f = next(f for f in funnel if f["client"] == "Acme Plumbing")
    # 3 acme convs in window; only 2 are missed-call sourced (direct text-in is not)
    check("roi acme: 2 missed calls", acme_f["missed_calls"] == 2)
    check("roi acme: 3 texted", acme_f["texted"] == 3)
    check("roi acme: 2 replied", acme_f["replied"] == 2)
    check("roi acme: 1 booked", acme_f["booked"] == 1)
    check("roi acme: reply rate 2/3", abs(acme_f["reply_rate"] - 2 / 3) < 0.001)
    check("roi acme: booking rate 1/3", abs(acme_f["booking_rate"] - 1 / 3) < 0.001)
    plumb_f = next(f for f in funnel if f["client"] == "plumbing")
    check("roi legacy: missed+texted", plumb_f["missed_calls"] == 1 and plumb_f["texted"] == 1)
    # render table does not crash + revenue math
    tbl = roi.render_table(funnel, avg_job_value=350)
    check("roi: table renders", "Acme Plumbing" in tbl and "350" in tbl)
    conn.close()
    path.unlink()


# ---------------------------------------------------------------------------
# 2. review_queue
# ---------------------------------------------------------------------------
def test_review_queue() -> None:
    import review_queue as rq

    conn, path = make_db()
    acme = seed_client(conn, "Acme Plumbing", "plumbing")
    # unanswered: active conv, last message is user
    seed_conv(conn, "+14065551111", acme, "plumbing", state="active",
              response_count=1, user_msgs=1)
    # maxed_unbooked: hit cap without booking
    seed_conv(conn, "+14065552222", acme, "plumbing", state="completed",
              branch="none", response_count=10, max_responses=10, user_msgs=1)
    # stale_quiet: active, zero messages, 3 days old
    old = (datetime.datetime.utcnow() - datetime.timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    seed_conv(conn, "+14065553333", acme, "plumbing", state="active",
              response_count=0, created=old, sys_msg=True, user_msgs=0)
    # healthy booked — must NOT appear
    seed_conv(conn, "+14065554444", acme, "plumbing", state="completed",
              branch="booked", response_count=4, user_msgs=3)
    conn.commit()

    since = datetime.datetime(2026, 8, 1, 0, 0, 0)
    queue = rq.build_queue(conn, since)
    reasons = {q["conv_id"]: q["reason"] for q in queue}
    check("review: 3 in queue", len(queue) == 3)
    check("review: unanswered detected", "unanswered" in reasons.values())
    check("review: maxed_unbooked detected", "maxed_unbooked" in reasons.values())
    check("review: stale_quiet detected", "stale_quiet" in reasons.values())
    check("review: booked conv excluded", len(queue) == 3)
    conn.close()
    path.unlink()


# ---------------------------------------------------------------------------
# 3. export_conversations
# ---------------------------------------------------------------------------
def test_export() -> None:
    import export_conversations as ex

    conn, path = make_db()
    acme = seed_client(conn, "Acme Plumbing", "plumbing")
    seed_conv(conn, "+14065551111", acme, "plumbing", branch="booked",
              response_count=3, meta={"_booked_event": "evt_1",
                                      "customer_name": "Tom",
                                      "appt_date": "2026-08-19",
                                      "appt_time": "09:00 AM"},
              sys_msg=True, ai_msgs=1, user_msgs=2)
    conn.commit()

    since = datetime.datetime(2026, 8, 1, 0, 0, 0)
    convs = ex.fetch(conn, since, client_filter=None)
    check("export: fetched 1 conv", len(convs) == 1)
    c = convs[0]
    check("export: client name", c["client"] == "Acme Plumbing")
    check("export: phone carried", c["phone"] == "+14065551111")
    check("export: branch", c["branch"] == "booked")
    check("export: booking meta", c["customer_name"] == "Tom" and c["appt_date"] == "2026-08-19")
    md = ex.render_markdown(convs)
    check("export: md has transcript", "👤 " in md and "🤖 AI" in md and "Missed call" in md)
    csv_out = ex.render_csv(convs)
    check("export: csv header", csv_out.splitlines()[0].startswith("conv_id,client,phone"))
    check("export: csv row", "Acme Plumbing" in csv_out and "+14065551111" in csv_out)
    # client filter
    convs2 = ex.fetch(conn, since, client_filter="Acme Plumbing")
    check("export: filter matches", len(convs2) == 1)
    convs3 = ex.fetch(conn, since, client_filter="Nope LLC")
    check("export: filter no match", len(convs3) == 0)
    conn.close()
    path.unlink()


# ---------------------------------------------------------------------------
# 4. multi-touch follow-up (real automation.py against temp DB)
# ---------------------------------------------------------------------------
def test_follow_up_multitouch() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="hermes-test-")
    os.close(fd)
    os.environ["DB_PATH"] = db_path
    # import AFTER DB_PATH is set — config lru_cache reads it on first call
    from database import get_session, init_db, ScheduledTask  # noqa: E402
    from automation import schedule_follow_up, cancel_follow_ups  # noqa: E402
    init_db()

    tasks = schedule_follow_up("+14065551234", 1, business_name="Acme Plumbing")
    check("followup: two tasks", len(tasks) == 2)
    kinds = [t.kind for t in tasks]
    check("followup: kinds follow_up+follow_up_2", sorted(kinds) == ["follow_up", "follow_up_2"])
    check("followup: different bodies", tasks[0].body != tasks[1].body)
    check("followup: both have opt-out",
          all("Reply STOP to opt out" in t.body for t in tasks))
    check("followup: second is later",
          tasks[1].run_at > tasks[0].run_at)

    # cancel must kill BOTH
    cancelled = cancel_follow_ups("+14065551234", 1)
    check("followup: cancel both", cancelled == 2)
    db = get_session()
    try:
        pending = db.query(ScheduledTask).filter(
            ScheduledTask.status == "pending").all()
        check("followup: nothing pending after cancel", len(pending) == 0)
    finally:
        db.close()
    from database import engine  # noqa: E402
    engine.dispose()  # release the file lock so the temp DB can be deleted
    Path(db_path).unlink()


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    if FAILS:
        print(f"FAIL ({len(FAILS)}): {', '.join(FAILS)}")
        return 1
    print(f"PASS: {len(tests)} roi/review/export/follow-up checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
