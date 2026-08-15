"""Review Engine (#17) functional test — stubbed senders, temp DB.

No real SMS/email is sent: send_sms and send_email are replaced with
recording stubs. Verifies scheduling, timings, opt-out/reviewed skip
logic, and the /job-completed + Telnyx webhook keyword endpoints.
"""
import os
import sys
import tempfile
import datetime
from pathlib import Path

# --- temp DB BEFORE importing app/database ---
_tmp = tempfile.mkdtemp(prefix="review_engine_test_")
os.environ["DB_PATH"] = str(Path(_tmp) / "test.db")
os.environ["BUSINESS_NAME"] = "Test Plumbing Co"
os.environ["REVIEW_LINK"] = "https://g.page/r/test123"
os.environ["REVIEW_FOLLOW_UP_HOURS"] = "48"
os.environ["REVIEW_EMAIL_DELAY_HOURS"] = "72"
# Force warn-and-accept webhook mode: the real account public key lives in
# .env and would 403 these unsigned test webhooks. Signature verification is
# covered separately (telnyx_client.verify_webhook_signature); these tests
# exercise the keyword/compliance handling, not crypto.
os.environ["TELNYX_WEBHOOK_PUBLIC_KEY"] = ""
# SMTP intentionally unset -> email fallback logs and returns False (not configured)

sys.path.insert(0, str(Path(__file__).parent))

SMS_LOG: list[tuple] = []
EMAIL_LOG: list[tuple] = []

import automation
from automation import schedule_completion_flow, _review_engine_skip, _fire_due_tasks
from database import init_db, get_session, ScheduledTask, Contact

# Stub the senders at their call sites
automation.send_sms = lambda to, body: (SMS_LOG.append((to, body)) or "SMSTUB")
automation.send_email = lambda to, subj, body: (EMAIL_LOG.append((to, subj, body)) or True)

init_db()

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------------------
# 1. schedule_completion_flow creates 4 tasks with right kinds + timings
# ---------------------------------------------------------------------------
def fetch_tasks(phone):
    db = get_session()
    try:
        rows = db.query(ScheduledTask).filter(ScheduledTask.phone_number == phone).all()
        return [(t.kind, t.run_at, t.status, t.payload) for t in rows]
    finally:
        db.close()


completed = datetime.datetime(2026, 8, 10, 18, 0, 0)  # UTC
schedule_completion_flow(
    phone_number="+14065550001",
    customer_name="Jane",
    service_type="drain cleaning",
    completed_at=completed,
    email="jane@example.com",
)

rows = fetch_tasks("+14065550001")
kinds = [r[0] for r in rows]
run_at = {k: dt for k, dt, _, _ in rows}
check("4 tasks scheduled", len(rows) == 4, f"got {kinds}")
check("kinds correct", kinds == ["thank_you", "review", "review_follow_up", "review_email"], str(kinds))

thank_you_row = [r for r in rows if r[0] == "thank_you"][0]
review_row = [r for r in rows if r[0] == "review"][0]
follow_up_row = [r for r in rows if r[0] == "review_follow_up"][0]
email_row = [r for r in rows if r[0] == "review_email"][0]

check("thank_you at T+2h", run_at["thank_you"] == completed + datetime.timedelta(hours=2),
      run_at["thank_you"].isoformat())
check("review at T+2h+15m", run_at["review"] == completed + datetime.timedelta(hours=2, minutes=15),
      run_at["review"].isoformat())
check("follow_up at T+2h+48h", run_at["review_follow_up"] == completed + datetime.timedelta(hours=50),
      run_at["review_follow_up"].isoformat())
check("email at T+2h+72h", run_at["review_email"] == completed + datetime.timedelta(hours=74),
      run_at["review_email"].isoformat())
check("email payload has email", (email_row[3] or {}).get("email") == "jane@example.com",
      str(email_row[3]))

# Fetch bodies for content checks
def fetch_bodies(phone):
    db = get_session()
    try:
        rows = db.query(ScheduledTask).filter(ScheduledTask.phone_number == phone).all()
        return {t.kind: t.body for t in rows}
    finally:
        db.close()

bodies = fetch_bodies("+14065550001")
check("review body has link + opt-out", "g.page" in bodies["review"] and "STOP" in bodies["review"], bodies["review"])
check("thank_you body has opt-out", "STOP" in bodies["thank_you"], bodies["thank_you"])

# ---------------------------------------------------------------------------
# 2. Worker skips: contact with no tags -> follow-up fires when due
# ---------------------------------------------------------------------------
SMS_LOG.clear()
EMAIL_LOG.clear()
db = get_session()
# Force follow-up + email due now
db.query(ScheduledTask).filter(ScheduledTask.phone_number == "+14065550001").update(
    {ScheduledTask.run_at: datetime.datetime.utcnow() - datetime.timedelta(seconds=5)}
)
db.commit()
db.close()

_fire_due_tasks(max_per_tick=20)
check("no-review contact: thank_you sent", any("Thank you" in b or "Thanks" in b for _, b in SMS_LOG), str(SMS_LOG))
check("no-review contact: review sent", any("review" in b.lower() and "g.page" in b for _, b in SMS_LOG), str(SMS_LOG))
check("no-review contact: follow_up sent", any("nudge" in b.lower() for _, b in SMS_LOG), str(SMS_LOG))
check("no-review contact: email sent", len(EMAIL_LOG) == 1, str(EMAIL_LOG))

# ---------------------------------------------------------------------------
# 3. Opt-out: contact replies STOP -> all pending cancelled, new flow skipped
# ---------------------------------------------------------------------------
SMS_LOG.clear()
EMAIL_LOG.clear()
db = get_session()
# The contact must exist for tag checks to apply
existing = db.query(Contact).filter(Contact.phone_number == "+14065550001").first()
if not existing:
    db.add(Contact(phone_number="+14065550001", tags=[]))
    db.commit()
contact = db.query(Contact).filter(Contact.phone_number == "+14065550001").first()
contact.tags = list(contact.tags or []) + ["opted-out"]
db.commit()

# Schedule a fresh flow for the opted-out contact
schedule_completion_flow(
    phone_number="+14065550001", customer_name="Jane",
    service_type="drain cleaning", completed_at=completed, email="jane@example.com",
)
db = get_session()
db.query(ScheduledTask).filter(ScheduledTask.phone_number == "+14065550001").update(
    {ScheduledTask.run_at: datetime.datetime.utcnow() - datetime.timedelta(seconds=5)}
)
db.commit()
db.close()

_fire_due_tasks(max_per_tick=20)
check("opted-out: no SMS sent", len(SMS_LOG) == 0, str(SMS_LOG))
check("opted-out: no email sent", len(EMAIL_LOG) == 0, str(EMAIL_LOG))

db = get_session()
statuses = {t.kind: t.status for t in db.query(ScheduledTask).filter(ScheduledTask.phone_number == "+14065550001").all()}
db.close()
check("opted-out: follow_up cancelled", statuses.get("review_follow_up") == "cancelled", str(statuses))
check("opted-out: email cancelled", statuses.get("review_email") == "cancelled", str(statuses))

# ---------------------------------------------------------------------------
# 4. Reviewed: contact replies DONE -> follow-up + email skipped
# ---------------------------------------------------------------------------
SMS_LOG.clear()
EMAIL_LOG.clear()
db = get_session()
contact = db.query(Contact).filter(Contact.phone_number == "+14065550001").first()
contact.tags = ["reviewed"]  # reset: reviewed but NOT opted-out
db.commit()
db.close()

schedule_completion_flow(
    phone_number="+14065550001", customer_name="Jane",
    service_type="drain cleaning", completed_at=completed, email="jane@example.com",
)
db = get_session()
db.query(ScheduledTask).filter(ScheduledTask.phone_number == "+14065550001").update(
    {ScheduledTask.run_at: datetime.datetime.utcnow() - datetime.timedelta(seconds=5)}
)
db.commit()
db.close()

_fire_due_tasks(max_per_tick=20)
check("reviewed: thank_you + review sent", len(SMS_LOG) == 2, str(SMS_LOG))
check("reviewed: no follow_up sent", not any("nudge" in b.lower() for _, b in SMS_LOG), str(SMS_LOG))
check("reviewed: no email sent", len(EMAIL_LOG) == 0, str(EMAIL_LOG))

# ---------------------------------------------------------------------------
# 5. Endpoint: /job-completed via TestClient
# ---------------------------------------------------------------------------
from fastapi.testclient import TestClient
import app as app_module

# app imports send_sms from telnyx_client directly — stub at call site
import telnyx_client
telnyx_client.send_sms = lambda to, body, from_number=None: (SMS_LOG.append((to, body)) or "SMSTUB")

with TestClient(app_module.app) as client:
    r = client.post("/job-completed", json={
        "phone": "+14065550002",
        "customer_name": "Bob",
        "service_type": "water heater",
        "completed_at": "2026-08-11T12:00:00",
        "email": "bob@example.com",
    })
    check("endpoint 200", r.status_code == 200, str(r.status_code))
    body = r.json()
    check("endpoint schedules 4", len(body.get("scheduled", [])) == 4, str(body))

    r_bad = client.post("/job-completed", json={"phone": ""})
    check("endpoint rejects missing phone", r_bad.status_code == 400, str(r_bad.status_code))

    # /sms opt-out keyword tags contact + cancels
    db = get_session()
    c = Contact(phone_number="+14065550003", tags=[])
    db.add(c)
    db.commit()
    db.close()

    r_stop = client.post("/webhooks/telnyx", json={
        "data": {"event_type": "message.received", "payload": {"from": "+14065550003", "text": "STOP"}}
    })
    check("/webhooks/telnyx STOP 200", r_stop.status_code == 200, str(r_stop.status_code))
    db = get_session()
    c = db.query(Contact).filter(Contact.phone_number == "+14065550003").first()
    check("STOP tags contact opted-out", "opted-out" in (c.tags or []), str(c.tags))
    db.close()

    # Upsert path: STOP with NO prior contact must still persist the tag
    # (or a future /job-completed would message an opted-out customer)
    r_stop_new = client.post("/webhooks/telnyx", json={
        "data": {"event_type": "message.received", "payload": {"from": "+14065550009", "text": "STOP"}}
    })
    db = get_session()
    c9 = db.query(Contact).filter(Contact.phone_number == "+14065550009").first()
    check("STOP upserts contact + tags", c9 is not None and "opted-out" in (c9.tags or []),
          f"contact={c9 is not None} tags={c9.tags if c9 else None}")
    db.close()

    # /sms DONE keyword tags reviewed
    r_done = client.post("/webhooks/telnyx", json={
        "data": {"event_type": "message.received", "payload": {"from": "+14065550003", "text": "DONE"}}
    })
    db = get_session()
    c = db.query(Contact).filter(Contact.phone_number == "+14065550003").first()
    check("DONE tags contact reviewed", "reviewed" in (c.tags or []), str(c.tags))
    db.close()

    # conversational message still flows (no active conv -> ignored, not tagged)
    r_talk = client.post("/webhooks/telnyx", json={
        "data": {"event_type": "message.received", "payload": {"from": "+14065550003", "text": "can I cancel my appointment?"}}
    })
    check("conversational text not treated as keyword", r_talk.status_code == 200)

print()
if failures:
    print(f"FAILED: {len(failures)} — {failures}")
    sys.exit(1)
print("ALL TESTS PASSED")
