"""Automation layer — reminders, follow-ups, review requests, business alerts.

Schedules and fires outbound SMS on top of the conversation engine:

- reminder:  ~24h before a booked appointment  ("reply C to confirm")
- follow_up: 24h after initial text if the lead went quiet
- review:    after a booked appointment time passes ("leave a review")
- business alert: notify the owner the moment a booking lands

All times are stored as UTC datetimes in the ScheduledTask table; the
worker loop (run_task_worker) polls for due tasks and sends them.
"""

from __future__ import annotations

import asyncio
import datetime
import logging

from config import get_settings
from database import get_session, ScheduledTask
from telnyx_client import send_sms
from email_client import send_email

logger = logging.getLogger("missed-call-ai")

settings = get_settings()

# ---------------------------------------------------------------------------
# Scheduling helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime.datetime:
    return datetime.datetime.utcnow()


def schedule_task(
    kind: str,
    phone_number: str,
    run_at: datetime.datetime,
    body: str,
    conversation_id: int | None = None,
) -> ScheduledTask | None:
    """Persist a scheduled outbound SMS. Returns the row or None on error."""
    db = get_session()
    try:
        task = ScheduledTask(
            kind=kind,
            phone_number=phone_number,
            conversation_id=conversation_id,
            run_at=run_at,
            body=body,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)  # load id before session closes (expire-on-commit)
        logger.info(
            "Scheduled %s for %s at %s (conv %s)",
            kind, phone_number, run_at.isoformat(), conversation_id,
        )
        return task
    except Exception as e:
        db.rollback()
        logger.error("Failed to schedule %s for %s: %s", kind, phone_number, e)
        return None
    finally:
        db.close()


def cancel_tasks(kind: str, phone_number: str, conversation_id: int | None = None) -> int:
    """Cancel pending tasks of a kind for a phone/conversation."""
    db = get_session()
    try:
        q = db.query(ScheduledTask).filter(
            ScheduledTask.kind == kind,
            ScheduledTask.phone_number == phone_number,
            ScheduledTask.status == "pending",
        )
        if conversation_id is not None:
            q = q.filter(ScheduledTask.conversation_id == conversation_id)
        rows = q.all()
        for r in rows:
            r.status = "cancelled"
        db.commit()
        return len(rows)
    except Exception as e:
        db.rollback()
        logger.error("Failed to cancel %s tasks: %s", kind, e)
        return 0
    finally:
        db.close()


def cancel_tasks_of_phone(phone_number: str) -> int:
    """Cancel ALL pending tasks for a phone (opt-out). Returns count cancelled."""
    db = get_session()
    try:
        rows = (
            db.query(ScheduledTask)
            .filter(
                ScheduledTask.phone_number == phone_number,
                ScheduledTask.status == "pending",
            )
            .all()
        )
        for r in rows:
            r.status = "cancelled"
        db.commit()
        return len(rows)
    except Exception as e:
        db.rollback()
        logger.error("Failed to cancel tasks for %s: %s", phone_number, e)
        return 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Feature: business booking alert
# ---------------------------------------------------------------------------


def notify_business_booked(
    customer_name: str,
    appt_date: str,
    appt_time: str,
    service_type: str,
    business_owner_phone: str | None = None,
) -> None:
    """Text the business owner the moment an appointment is booked."""
    owner = business_owner_phone or settings.business_owner_phone
    if not owner:
        logger.warning("No business owner phone configured — skipping booking alert")
        return
    body = (
        f"✅ BOOKED: {customer_name} for {service_type} — "
        f"{appt_time} on {appt_date}. It's on your calendar + sheet."
    )
    send_sms(owner, body)


# ---------------------------------------------------------------------------
# Feature: appointment reminder (24h before)
# ---------------------------------------------------------------------------


def schedule_reminder(
    phone_number: str,
    appt_date: str,
    appt_time: str,
    business_name: str | None = None,
    service_type: str | None = None,
    conversation_id: int | None = None,
) -> ScheduledTask | None:
    """Schedule a reminder ~24h before the appointment (min 6h lead time)."""
    appt_dt = _parse_appt(appt_date, appt_time)
    if appt_dt is None:
        logger.warning("Couldn't parse appointment %s %s — no reminder", appt_date, appt_time)
        return None

    run_at = appt_dt - datetime.timedelta(hours=24)
    now = _utcnow()
    # If appointment is <6h away, don't bother with a reminder
    if run_at < now + datetime.timedelta(hours=6):
        logger.info("Appointment too soon for reminder — skipping")
        return None

    biz = business_name or settings.business_name
    body = (
        f"Reminder: {biz}{' — ' + service_type if service_type else ''} "
        f"tomorrow at {appt_time} on {appt_date}. "
        f"Reply C to confirm, R to reschedule."
    )
    return schedule_task("reminder", phone_number, run_at, body, conversation_id)


# ---------------------------------------------------------------------------
# Feature: follow-up (24h after initial text, if quiet)
# ---------------------------------------------------------------------------


def cancel_follow_ups(phone_number: str, conversation_id: int | None = None) -> int:
    """Cancel ALL pending no-reply follow-up touches for a phone/conversation.

    Covers both kinds ('follow_up' + 'follow_up_2') — a customer reply, a
    booking, or an unqualified close must stop the whole nudge sequence, not
    just the first touch (2026-08-18, HighLevel multi-touch pattern).
    """
    total = 0
    for kind in ("follow_up", "follow_up_2"):
        total += cancel_tasks(kind, phone_number, conversation_id)
    return total


def schedule_follow_up(
    phone_number: str,
    conversation_id: int | None,
    business_name: str | None = None,
) -> list[ScheduledTask]:
    """Schedule TWO no-reply follow-ups with DIFFERENT angles (2026-08-18).

    Follows HighLevel's multi-touch quiet-lead pattern: if the customer never
    replies to the initial text-back, nudge once at +FOLLOW_UP_HOURS (24h) and
    again at +FOLLOW_UP_2_HOURS (48h) with different copy and a booking link
    angle on the second touch. Every nudge carries the opt-out line (Google
    10DLC compliance — the old single follow-up had no opt-out).

    Both are cancelled by cancel_follow_ups() when the customer replies,
    books, or goes unqualified.
    """
    run_at = _utcnow() + datetime.timedelta(hours=settings.follow_up_hours)
    run_at_2 = _utcnow() + datetime.timedelta(hours=settings.follow_up_2_hours)
    biz = business_name or settings.business_name

    body_1 = (
        f"Hey! Just following up from {biz} — we tried reaching you earlier "
        f"about your call. Still need help? Reply here and we'll get you sorted. "
        f"Reply STOP to opt out."
    )
    body_2 = (
        f"One more from {biz} — if you're still after {settings.service_area} "
        f"help, text us what you need and we'll get it on the calendar. "
        f"No phone call required. Reply STOP to opt out."
    )
    tasks: list[ScheduledTask] = []
    t1 = schedule_task("follow_up", phone_number, run_at, body_1, conversation_id)
    if t1:
        tasks.append(t1)
    t2 = schedule_task("follow_up_2", phone_number, run_at_2, body_2, conversation_id)
    if t2:
        tasks.append(t2)
    return tasks


# ---------------------------------------------------------------------------
# Feature: review request (after appointment time passes)
# ---------------------------------------------------------------------------


def schedule_review_request(
    phone_number: str,
    appt_date: str,
    appt_time: str,
    business_name: str | None = None,
    service_type: str | None = None,
    conversation_id: int | None = None,
    review_link: str | None = None,
) -> ScheduledTask | None:
    """Schedule a Google-review request shortly after the appointment."""
    appt_dt = _parse_appt(appt_date, appt_time)
    if appt_dt is None:
        return None

    run_at = appt_dt + datetime.timedelta(hours=2)
    biz = business_name or settings.business_name
    # Per-business link (from prompt YAML) wins; fall back to global env
    link = review_link or settings.review_link
    link_line = f"\nLeave a review here: {link}" if link else ""
    body = (
        f"Hope your {service_type or 'appointment'} with {biz} went well! "
        f"We'd love your feedback.{link_line} "
        f"Reply STOP to opt out of future messages."
    )
    return schedule_task("review", phone_number, run_at, body, conversation_id)


# ---------------------------------------------------------------------------
# Feature: Review Engine (#17) — job-completed trigger
# ---------------------------------------------------------------------------


def schedule_completion_flow(
    phone_number: str,
    customer_name: str = "",
    service_type: str = "",
    completed_at: datetime.datetime | None = None,
    email: str = "",
    review_link: str | None = None,
    conversation_id: int | None = None,
) -> list[ScheduledTask]:
    """Trigger the full post-service sequence when a job is marked completed.

    Schedules (all times UTC):
      T+2h           — thank-you-for-your-business text
      T+2h + 15min   — Google review request text (with link + opt-out)
      T+2h + review_follow_up_hours — one gentle follow-up text,
                     SKIPPED at fire time if customer already reviewed
                     or opted out
      T+2h + review_email_delay_hours — email fallback with the same ask,
                     SKIPPED if no email on file / already reviewed / opted out

    Returns the list of created ScheduledTask rows.
    """
    base = completed_at or _utcnow()
    t2 = base + datetime.timedelta(hours=2)
    biz = settings.business_name
    link = review_link or settings.review_link
    name_part = f", {customer_name}" if customer_name else ""

    tasks: list[ScheduledTask] = []

    # 1. Thank-you (T+2h) — every message carries the opt-out (Google rules)
    thank_you = (
        f"Thanks for choosing {biz}{name_part}! We really appreciate your "
        f"business and hope your {service_type or 'service'} went great. "
        f"Reply STOP to opt out of future messages."
    )
    t = schedule_task("thank_you", phone_number, t2, thank_you, conversation_id)
    if t:
        tasks.append(t)

    # 2. Review request (T+2h + 15min — stagger so they don't stack as one)
    link_line = f"\nLeave a review here: {link}" if link else ""
    review = (
        f"Quick favor — if you're happy with your {service_type or 'service'} "
        f"from {biz}, a review would mean the world to us.{link_line} "
        f"Reply DONE once you've left it, or STOP to opt out."
    )
    t = schedule_task(
        "review", phone_number, t2 + datetime.timedelta(minutes=15), review, conversation_id
    )
    if t:
        tasks.append(t)

    # 3. Follow-up text (skip at fire time if reviewed/opted-out)
    fu_body = (
        f"Just a friendly nudge from {biz} — if you haven't yet, we'd love "
        f"your feedback on your recent {service_type or 'service'}.{link_line} "
        f"Reply DONE if you've left one, or STOP to opt out."
    )
    t = schedule_task(
        "review_follow_up",
        phone_number,
        t2 + datetime.timedelta(hours=settings.review_follow_up_hours),
        fu_body,
        conversation_id,
    )
    if t:
        tasks.append(t)

    # 4. Email fallback (skip at fire time if no email / reviewed / opted-out)
    email_body = (
        f"Hi{customer_name and ' ' + customer_name or ''},\n\n"
        f"We hope your recent {service_type or 'service'} with {biz} went well.\n"
        f"Your feedback helps us improve — it takes less than a minute:\n"
        f"{link or ''}\n\n"
        f"Reply to this email to opt out of future messages.\n\n"
        f"Thanks,\n{biz}"
    )
    t = schedule_task(
        "review_email",
        phone_number,
        t2 + datetime.timedelta(hours=settings.review_email_delay_hours),
        email_body,
        conversation_id,
    )
    if t:
        db = get_session()
        try:
            row = db.get(ScheduledTask, t.id)  # re-fetch: t is detached
            if row:
                row.payload = {
                    "customer_name": customer_name,
                    "service_type": service_type,
                    "review_link": link,
                    "email": email,
                }
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error("Failed to save review_email payload: %s", e)
        finally:
            db.close()
        tasks.append(t)

    logger.info(
        "Review Engine: scheduled %d task(s) for %s (completed_at=%s)",
        len(tasks), phone_number, base.isoformat(),
    )
    return tasks


def _contact_tags(phone_number: str) -> list[str]:
    """Return the tags for a contact (empty list if none)."""
    db = get_session()
    try:
        from database import Contact
        contact = (
            db.query(Contact)
            .filter(Contact.phone_number == phone_number)
            .first()
        )
        return list(contact.tags or []) if contact else []
    except Exception as e:
        logger.error("Error reading tags for %s: %s", phone_number, e)
        return []
    finally:
        db.close()


def _review_engine_skip(task: ScheduledTask) -> bool:
    """True if a Review Engine task should NOT be sent.

    - opted-out contact: skip EVERYTHING (thank-you, review, follow-up, email)
      — they replied STOP; compliance requires zero further messages.
    - reviewed contact: skip only the nagging tail (follow-up + email).
    - review_email with no address on file: skip.
    """
    tags = _contact_tags(task.phone_number)
    if "opted-out" in tags:
        logger.info("Skipping %s for %s — contact opted out", task.kind, task.phone_number)
        return True
    if "reviewed" in tags and task.kind in ("review_follow_up", "review_email"):
        logger.info("Skipping %s for %s — contact already reviewed", task.kind, task.phone_number)
        return True
    if task.kind == "review_email":
        email = (task.payload or {}).get("email", "")
        if not email:
            logger.info("Skipping review_email for %s — no email on file", task.phone_number)
            return True
    return False


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------


async def run_task_worker(interval_seconds: int = 30, max_per_tick: int = 20) -> None:
    """Background loop: fire any due scheduled tasks."""
    logger.info("Task worker started (interval=%ss)", interval_seconds)
    while True:
        try:
            await asyncio.to_thread(_fire_due_tasks, max_per_tick)
        except Exception as e:
            logger.error("Task worker error: %s", e)
        await asyncio.sleep(interval_seconds)


def _fire_due_tasks(max_per_tick: int = 20) -> int:
    """Send all due tasks. Returns count sent."""
    db = get_session()
    sent = 0
    try:
        due = (
            db.query(ScheduledTask)
            .filter(
                ScheduledTask.status == "pending",
                ScheduledTask.run_at <= _utcnow(),
            )
            .order_by(ScheduledTask.run_at.asc())
            .limit(max_per_tick)
            .all()
        )
        for task in due:
            # Review Engine skips: opted-out = nothing further at all;
            # reviewed = no follow-up/email nagging.
            if task.kind in ("thank_you", "review", "review_follow_up", "review_email") and _review_engine_skip(task):
                task.status = "cancelled"
                task.sent_at = _utcnow()
                logger.info("Skipped %s for %s (review/opt-out rules)", task.kind, task.phone_number)
                sent += 1
                continue

            if task.kind == "review_email":
                ok = send_email(
                    (task.payload or {}).get("email", ""),
                    f"How was your recent service? — {settings.business_name}",
                    task.body,
                )
            else:
                ok = bool(send_sms(task.phone_number, task.body))

            if ok:
                task.status = "sent"
                task.sent_at = _utcnow()
                logger.info("Sent %s to %s (task #%s)", task.kind, task.phone_number, task.id)
            else:
                task.attempts = (task.attempts or 0) + 1
                if task.attempts >= 3:
                    task.status = "failed"
                logger.error("Send failed for task #%s (%s) — attempt %s",
                             task.id, task.kind, task.attempts)
            sent += 1
        db.commit()
        return sent
    except Exception as e:
        db.rollback()
        logger.error("Task worker tick error: %s", e)
        return sent
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _parse_appt(date_str: str, time_str: str) -> datetime.datetime | None:
    """Parse '2026-08-03' + '10:00 AM' (MT) into an aware-UTC datetime."""
    try:
        if "AM" in time_str.upper() or "PM" in time_str.upper():
            naive = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I:%M %p")
        else:
            naive = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None
    # Montana is MDT (UTC-6) in summer; store UTC for the scheduler
    return naive + datetime.timedelta(hours=6)
