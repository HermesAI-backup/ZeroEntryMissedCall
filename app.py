"""FastAPI server — Telnyx SMS webhooks for the missed-call AI text-back system.

Twilio scrubbed Aug 10 (per Sevin): no Twilio code, config, or routes remain.
Inbound SMS arrives via the Telnyx messaging-profile webhook (/webhooks/telnyx);
all outbound SMS goes through Telnyx. The inbound-call trigger that used to be
Twilio's /voice webhook is gone — the missed-call reach-out routine
(_handle_missed_call) is kept for a future Telnyx voice / call-forwarding path.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form
from fastapi.responses import PlainTextResponse, JSONResponse

from config import get_settings, PROJECT_ROOT
from database import init_db, get_session, Contact, Conversation, Message, WhitelistEntry
from conversation import ConversationEngine
from telnyx_client import send_sms, verify_webhook_signature
from automation import (
    notify_business_booked,
    schedule_follow_up,
    schedule_reminder,
    schedule_review_request,
    schedule_completion_flow,
    cancel_tasks,
    cancel_tasks_of_phone,
    run_task_worker,
    _parse_appt,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("missed-call-ai")

settings = get_settings()

# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info(f"Database initialized at {settings.db_path}")
    worker = asyncio.create_task(run_task_worker())
    logger.info("Automation worker started")
    yield
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Missed Call AI", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Missed-call reach-out (SMS)
#
# NOTE: The inbound-call trigger was Twilio's /voice webhook (scrubbed Aug 10).
# This routine is kept for a future Telnyx voice / call-forwarding integration
# to call; nothing wires it up today.
# ---------------------------------------------------------------------------


async def _handle_missed_call(phone_number: str):
    """Wait a human-like delay, then text the missed caller."""
    # Check whitelist — skip AI for known contacts
    db = get_session()
    try:
        whitelisted = (
            db.query(WhitelistEntry)
            .filter(WhitelistEntry.phone_number == phone_number)
            .first()
        )
        if whitelisted:
            logger.info(
                "Whitelisted caller %s (%s) — skipping AI text-back",
                phone_number,
                whitelisted.name or "unknown",
            )
            return

        # Dedupe: skip if we already texted this number in the last 10 min.
        # Prevents double-texting when a caller hangs up and immediately redials.
        ten_min_ago = datetime.datetime.utcnow() - datetime.timedelta(minutes=10)
        recent = (
            db.query(Conversation)
            .filter(
                Conversation.phone_number == phone_number,
                Conversation.last_ai_sent_at >= ten_min_ago,
            )
            .first()
        )
        if recent:
            logger.info(
                "Duplicate call from %s — already texted %s ago (conv #%d), skipping",
                phone_number,
                (datetime.datetime.utcnow() - recent.last_ai_sent_at).seconds,
                recent.id,
            )
            return
    finally:
        db.close()

    engine = ConversationEngine()
    delay = engine.delay_seconds()

    logger.info(
        "Missed call from %s — waiting %ds before reaching out",
        phone_number,
        delay,
    )
    await asyncio.sleep(delay)

    initial_text = engine.get_initial_message()

    # Persist conversation FIRST
    db = get_session()
    conversation = None
    try:
        # Upsert contact
        contact = (
            db.query(Contact)
            .filter(Contact.phone_number == phone_number)
            .first()
        )
        if not contact:
            contact = Contact(phone_number=phone_number, tags=["missed-call"])
            db.add(contact)
            db.flush()

        conversation = Conversation(
            contact_id=contact.id,
            phone_number=phone_number,
            business_type=engine.business_type,
            state="active",
            initial_delay_seconds=delay,
            max_responses=engine.max_responses(),
            response_count=0,
            last_ai_sent_at=datetime.datetime.utcnow(),
            metadata_json={},
        )
        db.add(conversation)
        db.flush()

        msg = Message(
            conversation_id=conversation.id,
            role="system",
            content=f"Missed call from {phone_number}",
        )
        db.add(msg)
        db.commit()
        logger.info("Conversation #%d created for %s", conversation.id, phone_number)
    except Exception as e:
        db.rollback()
        logger.error("Error creating missed-call conversation: %s", e)
        conversation = None
    finally:
        db.close()

    sid = send_sms(phone_number, initial_text, from_number=settings.outbound_from)
    if conversation is not None:
        db = get_session()
        try:
            msg = Message(
                conversation_id=conversation.id,
                role="assistant",
                content=initial_text,
            )
            db.add(msg)
            conversation.response_count = 1
            db.commit()
            logger.info(
                "Initial SMS sent to %s (conv #%d)", phone_number, conversation.id
            )
            # Schedule the 24h follow-up in case the lead goes quiet
            schedule_follow_up(phone_number, conversation.id, settings.business_name)
        except Exception as e:
            db.rollback()
            logger.error("Error saving initial SMS: %s", e)
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Webhook: Inbound SMS (Telnyx)
# ---------------------------------------------------------------------------


@app.post("/webhooks/telnyx")
async def telnyx_webhook(request: Request):
    """Receive inbound SMS via Telnyx webhook (event_type = message.received).

    Runs the same conversation logic as inbound SMS replies. Returns 200 with
    an empty body, which is what Telnyx expects for a delivered webhook.
    """
    raw = await request.body()
    # API v2 signing: telnyx-signature-ed25519 + telnyx-timestamp (message =
    # "{timestamp}|{raw}"). Tolerate the legacy X-Payload-Signature header too.
    sig = request.headers.get("telnyx-signature-ed25519") or request.headers.get(
        "X-Payload-Signature", ""
    )
    ts = request.headers.get("telnyx-timestamp", "")
    if not verify_webhook_signature(raw, sig, ts):
        logger.warning("Telnyx webhook rejected (bad or missing signature)")
        return JSONResponse({"error": "bad signature"}, status_code=403)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return PlainTextResponse("")
    ev = data.get("data", {})
    if ev.get("event_type") == "message.received":
        payload = ev.get("payload", {})
        frm = payload.get("from")
        body = payload.get("text", "")
        if frm:
            logger.info("Telnyx inbound SMS from %s: %s", frm, body[:80])
            await inbound_sms(From=frm, Body=body)
    return PlainTextResponse("")


async def inbound_sms(
    From: str,
    Body: str = "",
):
    """Handle an incoming SMS reply from a customer."""
    logger.info("Incoming SMS from %s: %s", From, Body[:80])

    # --- Whitelist commands ---
    upper = Body.strip().upper()
    if upper.startswith("WHITELIST") or upper.startswith("W") and len(Body.split()) >= 2:
        # WHITELIST +1406xxxxxxx or WHITELIST 406-xxx-xxxx
        import re
        parts = Body.split(None, 2)
        if len(parts) >= 2:
            raw_num = parts[1]
            digits = re.sub(r"\D", "", raw_num)
            if len(digits) == 10:
                digits = "+1" + digits
            elif len(digits) == 11:
                digits = "+" + digits
            name = parts[2] if len(parts) >= 3 else ""
            if len(digits) == 12:
                db = get_session()
                try:
                    existing = db.query(WhitelistEntry).filter(WhitelistEntry.phone_number == digits).first()
                    if existing:
                        existing.name = name or existing.name
                        msg = f"✅ {digits} already whitelisted. Name updated."
                    else:
                        entry = WhitelistEntry(phone_number=digits, name=name)
                        db.add(entry)
                        msg = f"✅ Whitelisted {digits}. Future calls from this number will ring through normally."
                    db.commit()
                    send_sms(From, msg)
                finally:
                    db.close()
                return PlainTextResponse("")
            else:
                send_sms(From, f"❌ Couldn't parse \"{raw_num}\". Send as: WHITELIST +1406xxxxxxx")
                return PlainTextResponse("")

    if upper.startswith("UNWHITELIST") or upper.startswith("UW"):
        parts = Body.split(None, 1)
        if len(parts) >= 2:
            raw_num = parts[1]
            digits = re.sub(r"\D", "", raw_num)
            if len(digits) == 10:
                digits = "+1" + digits
            elif len(digits) == 11:
                digits = "+" + digits
            if len(digits) == 12:
                db = get_session()
                try:
                    entry = db.query(WhitelistEntry).filter(WhitelistEntry.phone_number == digits).first()
                    if entry:
                        db.delete(entry)
                        db.commit()
                        send_sms(From, f"✅ Removed {digits} from whitelist.")
                    else:
                        send_sms(From, f"{digits} wasn't whitelisted.")
                finally:
                    db.close()
                return PlainTextResponse("")
            else:
                send_sms(From, f"❌ Couldn't parse \"{raw_num}\".")
                return PlainTextResponse("")

    if upper == "WHITELIST" or upper == "W LIST" or upper == "LIST":
        db = get_session()
        try:
            entries = db.query(WhitelistEntry).order_by(WhitelistEntry.added_at).all()
            if entries:
                lines = [f"{e.phone_number} ({e.name or 'no name'})" for e in entries]
                send_sms(From, "Whitelisted numbers:\n" + "\n".join(lines))
            else:
                send_sms(From, "No whitelisted numbers yet.")
        finally:
            db.close()
        return PlainTextResponse("")
    # --- End whitelist commands ---

    # --- Compliance keywords: opt-out + review-done ---
    # Full-body match only, so conversational messages ("can I cancel my
    # appointment?") still reach the AI. Carriers enforce these keywords at
    # the network level; we tag + cancel pending tasks here.
    OPT_OUT_KEYWORDS = {"STOP", "STOPALL", "STOP ALL", "UNSUBSCRIBE", "CANCEL", "QUIT", "END"}
    REVIEW_DONE_KEYWORDS = {"DONE", "DONE!", "REVIEWED", "LEFT", "LEFT IT", "DID IT"}

    if upper in OPT_OUT_KEYWORDS:
        db = get_session()
        try:
            contact = db.query(Contact).filter(Contact.phone_number == From).first()
            if not contact:
                # Upsert: opt-out must persist even without a prior conversation,
                # or a future /job-completed would message an opted-out customer.
                contact = Contact(phone_number=From, tags=["opted-out"], source="opt-out")
                db.add(contact)
            else:
                tags = list(contact.tags or [])
                if "opted-out" not in tags:
                    contact.tags = tags + ["opted-out"]
            db.commit()
            cancelled = cancel_tasks_of_phone(From)
            logger.info("Opt-out from %s — cancelled %d pending task(s)", From, cancelled)
        finally:
            db.close()
        return PlainTextResponse("")

    if upper in REVIEW_DONE_KEYWORDS:
        db = get_session()
        try:
            contact = db.query(Contact).filter(Contact.phone_number == From).first()
            if not contact:
                contact = Contact(phone_number=From, tags=["reviewed"], source="review-done")
                db.add(contact)
            else:
                tags = list(contact.tags or [])
                if "reviewed" not in tags:
                    contact.tags = tags + ["reviewed"]
            db.commit()
            for kind in ("review", "review_follow_up", "review_email"):
                cancel_tasks(kind, From)
            logger.info("Review done from %s — cancelled follow-up tasks", From)
        finally:
            db.close()
        return PlainTextResponse("")

    db = get_session()
    try:
        # Find active conversation for this number
        conversation = (
            db.query(Conversation)
            .filter(
                Conversation.phone_number == From,
                Conversation.state == "active",
            )
            .order_by(Conversation.id.desc())
            .first()
        )

        if not conversation:
            logger.info("No active conversation for %s — ignoring", From)
            return PlainTextResponse("")

        # Check response limit
        if conversation.response_count >= conversation.max_responses:
            logger.info(
                "Conv #%d hit max responses (%d) — marking completed",
                conversation.id,
                conversation.max_responses,
            )
            conversation.state = "completed"
            db.commit()
            return PlainTextResponse("")

        # Save incoming message
        msg_in = Message(
            conversation_id=conversation.id,
            role="user",
            content=Body,
        )
        db.add(msg_in)

        # Customer replied — cancel the quiet-lead follow-up (conversation is live)
        cancel_tasks("follow_up", From, conversation.id)

        # Build conversation history
        history_messages = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .order_by(Message.id)
            .all()
        )
        history = [
            {"role": m.role, "content": m.content} for m in history_messages
        ]

        # Generate AI reply
        engine = ConversationEngine(business_type=conversation.business_type)
        reply, branch, reason, booking_details = await engine.generate_reply(history)

        # Store booking details in conversation metadata for the scheduler
        meta = conversation.metadata_json or {}
        if booking_details:
            meta.update({k: v for k, v in booking_details.items() if v})
            conversation.metadata_json = meta

        # Send SMS
        sid = send_sms(From, reply)
        msg_out = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=reply,
        )
        db.add(msg_out)

        conversation.response_count = (history_messages[-1].id if history_messages else 0) + 1  # rough
        conversation.last_ai_sent_at = datetime.datetime.utcnow()

        # Handle branch detection
        if branch and branch != "none":
            conversation.branch = branch
            conversation.state = "completed"
            _handle_branch_action(db, conversation, branch, reason)
            logger.info(
                "Conv #%d branched to '%s': %s", conversation.id, branch, reason
            )

        db.commit()
        logger.info("Replied to %s (conv #%d): %s", From, conversation.id, reply[:60])

    except Exception as e:
        db.rollback()
        logger.error("Error processing SMS from %s: %s", From, e)
    finally:
        db.close()

    return PlainTextResponse("")


def _handle_branch_action(db, conversation: Conversation, branch: str, reason: str | None):
    """Execute side effects when a branch is hit — notifications, tags, etc."""
    contact = (
        db.query(Contact).filter(Contact.id == conversation.contact_id).first()
    )

    if branch == "booked":
        if contact:
            new_tags = list(set(contact.tags + ["booked-appointment"]))
            contact.tags = new_tags
        logger.info("BOOKED: %s — %s", conversation.phone_number, reason)

        # Extract booking details from conversation metadata
        meta = conversation.metadata_json or {}
        cust_name = meta.get("customer_name", "")
        cust_phone = meta.get("customer_phone", conversation.phone_number)
        address = meta.get("address", "")
        service_type = meta.get("service_type", settings.business_type)
        appt_date = meta.get("appt_date", "")
        appt_time = meta.get("appt_time", "10:00 AM")

        # Try to book via scheduler
        if appt_date and appt_time:
            from scheduler import book_appointment
            try:
                result = book_appointment(
                    date_str=appt_date,
                    time_str=appt_time,
                    customer_name=cust_name or reason or "Customer",
                    customer_phone=cust_phone,
                    address=address or "TBD",
                    service_type=service_type,
                    business_name=settings.business_name,
                )
                logger.info("SCHEDULER: %s — %s", result["status"], result.get("message", ""))
                if result["status"] == "booked":
                    # Notify business owner (real owner phone, not the AI number)
                    notify_business_booked(
                        customer_name=cust_name or reason or "Customer",
                        appt_date=appt_date,
                        appt_time=appt_time,
                        service_type=service_type,
                    )
                    # Schedule appointment reminder (24h before)
                    schedule_reminder(
                        phone_number=conversation.phone_number,
                        appt_date=appt_date,
                        appt_time=appt_time,
                        business_name=settings.business_name,
                        service_type=service_type,
                        conversation_id=conversation.id,
                    )
                    # Review Engine: thank-you + review request 2h after the
                    # appointment (job completion), plus follow-up + email fallback
                    engine_link = ConversationEngine(
                        business_type=conversation.business_type
                    ).review_link()
                    appt_dt = _parse_appt(appt_date, appt_time)
                    schedule_completion_flow(
                        phone_number=conversation.phone_number,
                        customer_name=cust_name or "",
                        service_type=service_type,
                        completed_at=appt_dt,
                        email=(contact.email if contact else "") or "",
                        review_link=engine_link or None,
                        conversation_id=conversation.id,
                    )
                    # Lead engaged — cancel the 24h follow-up
                    cancel_tasks("follow_up", conversation.phone_number, conversation.id)
            except Exception as e:
                logger.error("SCHEDULER error: %s", e)

    elif branch == "emergency":
        if contact:
            new_tags = list(set(contact.tags + ["emergency"]))
            contact.tags = new_tags
        logger.info(
            "EMERGENCY: %s — %s — notify business owner immediately!",
            conversation.phone_number,
            reason,
        )
        owner = settings.business_owner_phone
        if owner:
            send_sms(owner, f"🚨 EMERGENCY from {conversation.phone_number}: {reason}")
        else:
            logger.warning("EMERGENCY: BUSINESS_OWNER_PHONE not set — alert not sent")

    elif branch == "unqualified":
        if contact:
            new_tags = list(set(contact.tags + ["unqualified"]))
            contact.tags = new_tags
        logger.info("UNQUALIFIED: %s — %s", conversation.phone_number, reason)
        # Not interested — cancel the 24h follow-up
        cancel_tasks("follow_up", conversation.phone_number, conversation.id)

    elif branch == "hot_lead":
        if contact:
            new_tags = list(set(contact.tags + ["hot-lead"]))
            contact.tags = new_tags
        # Notify business owner about hot lead
        owner = settings.business_owner_phone
        if owner:
            send_sms(owner, f"🔥 HOT LEAD from {conversation.phone_number}: {reason}")
        else:
            logger.warning("HOT LEAD: BUSINESS_OWNER_PHONE not set — alert not sent")
        logger.info("HOT LEAD: %s — %s", conversation.phone_number, reason)


# ---------------------------------------------------------------------------
# Webhook: Inbound Call (Telnyx Call Control — missed-call trigger)
# ---------------------------------------------------------------------------


@app.post("/webhooks/telnyx/call")
async def telnyx_call_webhook(request: Request):
    """Handle Telnyx Call Control events.

    When a client forwards missed calls to our 406 number, Telnyx fires a
    call.initiated webhook here.  We reject the call (no one answers) and
    trigger the text-back conversation with the caller FROM our 406 number
    as if we are the client's business.
    """
    raw = await request.body()
    sig = request.headers.get("telnyx-signature-ed25519", "")
    ts = request.headers.get("telnyx-timestamp", "")
    if not verify_webhook_signature(raw, sig, ts):
        logger.warning("Telnyx call webhook rejected (bad or missing signature)")
        return JSONResponse({"error": "bad signature"}, status_code=403)

    try:
        data = await request.json()
    except Exception:
        return PlainTextResponse("")

    ev = data.get("data", {})
    event_type = ev.get("event_type", "")

    if event_type == "call.initiated":
        payload = ev.get("payload", {})
        from_number = (payload.get("from") or "").strip()
        if from_number:
            logger.info(
                "Inbound call from %s — triggering missed-call text-back", from_number
            )
            asyncio.create_task(_handle_missed_call(from_number))

        # Reject the call — nobody picks up, the AI texts back instead
        return PlainTextResponse(
            '<?xml version="1.0" encoding="UTF-8"?><Response><Reject/></Response>',
            media_type="application/xml",
        )

    return PlainTextResponse("")


# ---------------------------------------------------------------------------
# Webhook: Job Completed (Review Engine #17)
# ---------------------------------------------------------------------------


@app.post("/job-completed")
async def job_completed(request: Request):
    """Trigger the post-service Review Engine flow.

    Call this when a service/job is marked completed. Accepts JSON:
    {
      "phone": "+140****1234",        # required — customer's number
      "customer_name": "Jane",        # optional
      "service_type": "HVAC tune-up", # optional
      "completed_at": "2026-08-08T14:00:00",  # optional — defaults to now (UTC)
      "email": "jane@example.com",    # optional — enables email fallback
      "review_link": "https://g.page/r/...",  # optional — overrides env/prompt link
      "conversation_id": 123          # optional
    }

    Schedules: thank-you text (T+2h), review request text (T+2h+15m),
    follow-up text (T+2h+48h, skipped if reviewed/opted out), and an email
    fallback (T+2h+72h, skipped without an email on file).
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid JSON"}, status_code=400)

    phone = (payload.get("phone") or "").strip()
    if not phone:
        return JSONResponse({"status": "error", "message": "phone is required"}, status_code=400)

    completed_at = None
    raw_completed = payload.get("completed_at")
    if raw_completed:
        try:
            completed_at = datetime.datetime.fromisoformat(raw_completed)
            if completed_at.tzinfo is not None:
                completed_at = completed_at.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        except (ValueError, TypeError):
            return JSONResponse(
                {"status": "error", "message": "completed_at must be ISO-8601 (e.g. 2026-08-08T14:00:00)"},
                status_code=400,
            )

    tasks = schedule_completion_flow(
        phone_number=phone,
        customer_name=payload.get("customer_name", ""),
        service_type=payload.get("service_type", ""),
        completed_at=completed_at,
        email=payload.get("email", ""),
        review_link=payload.get("review_link"),
        conversation_id=payload.get("conversation_id"),
    )

    return {
        "status": "scheduled",
        "phone": phone,
        "scheduled": [t.kind for t in tasks],
        "note": "Thank-you + review request go out 2h after completion. "
                "Follow-up skipped if customer replies DONE or STOP.",
    }


# ---------------------------------------------------------------------------
# Admin / Debug endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "telnyx_configured": settings.telnyx_configured,
        "llm_configured": settings.llm_configured,
        "business_type": settings.business_type,
        "model": settings.llm_model,
    }


@app.get("/conversations")
async def list_conversations(limit: int = 20):
    db = get_session()
    try:
        conversations = (
            db.query(Conversation)
            .order_by(Conversation.id.desc())
            .limit(limit)
            .all()
        )
        results = []
        for c in conversations:
            phone_msgs = (
                db.query(Message)
                .filter(Message.conversation_id == c.id)
                .order_by(Message.id)
                .all()
            )
            results.append(
                {
                    "id": c.id,
                    "phone": c.phone_number,
                    "state": c.state,
                    "branch": c.branch,
                    "responses": c.response_count,
                    "messages": [
                        {"role": m.role, "content": m.content, "at": m.created_at.isoformat()}
                        for m in phone_msgs
                    ],
                }
            )
        return {"conversations": results}
    finally:
        db.close()


@app.get("/contacts")
async def list_contacts():
    db = get_session()
    try:
        contacts = db.query(Contact).order_by(Contact.id.desc()).limit(50).all()
        return {
            "contacts": [
                {
                    "id": c.id,
                    "phone": c.phone_number,
                    "name": f"{c.first_name} {c.last_name}".strip(),
                    "business": c.business_name,
                    "tags": c.tags,
                    "source": c.source,
                }
                for c in contacts
            ]
        }
    finally:
        db.close()
