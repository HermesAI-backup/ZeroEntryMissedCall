"""Test driver: fire the missed-call text-back with the PLUMBING persona.

Mirrors app._handle_missed_call() exactly (whitelist check, 35s delay,
conversation persistence, Telnyx send, follow-up scheduling) EXCEPT:
  - business_type forced to plumbing (env overrides before import)
  - 10-min dedupe skipped (we're firing 5 min after the sales test on purpose)

Also closes the previous sales conversation (conv 13) so the customer's
reply routes to the new plumbing conversation, and cancels its follow-up.
"""
import asyncio
import os

# Persona overrides — must be set BEFORE config import (Settings reads env at import)
os.environ["BUSINESS_TYPE"] = "plumbing"
os.environ["BUSINESS_NAME"] = "Helena Plumbing Co"
os.environ["SERVICE_AREA"] = "Helena, MT"

from app import (  # noqa: E402  (env must be set first)
    Contact,
    Conversation,
    Message,
    WhitelistEntry,
    get_session,
    logger,
    schedule_follow_up,
    settings,
)
from telnyx_client import send_sms  # noqa: E402
from conversation import ConversationEngine  # noqa: E402
from automation import cancel_tasks  # noqa: E402

TEST_PHONE = "+14064396365"  # Sevin's cell


async def main():
    db = get_session()
    try:
        # Close the sales conversation so replies route to the plumbing one
        prev = (
            db.query(Conversation)
            .filter(
                Conversation.phone_number == TEST_PHONE,
                Conversation.state == "active",
            )
            .order_by(Conversation.id.desc())
            .first()
        )
        if prev:
            prev.state = "completed"
            db.commit()
            logger.info("Closed previous conversation #%d (%s)", prev.id, prev.business_type)
            cancel_tasks("follow_up", TEST_PHONE, prev.id)
    finally:
        db.close()

    engine = ConversationEngine(business_type="plumbing")
    delay = engine.delay_seconds()
    logger.info("Missed call from %s — waiting %ds before reaching out", TEST_PHONE, delay)
    await asyncio.sleep(delay)

    initial_text = engine.get_initial_message()

    db = get_session()
    conversation = None
    try:
        contact = (
            db.query(Contact).filter(Contact.phone_number == TEST_PHONE).first()
        )
        if not contact:
            contact = Contact(phone_number=TEST_PHONE, tags=["missed-call"])
            db.add(contact)
            db.flush()
        conversation = Conversation(
            contact_id=contact.id,
            phone_number=TEST_PHONE,
            business_type=engine.business_type,
            state="active",
            initial_delay_seconds=delay,
            max_responses=engine.max_responses(),
            response_count=0,
            last_ai_sent_at=__import__("datetime").datetime.utcnow(),
            metadata_json={},
        )
        db.add(conversation)
        db.flush()
        db.add(Message(
            conversation_id=conversation.id,
            role="system",
            content=f"Missed call from {TEST_PHONE}",
        ))
        db.commit()
        logger.info("Conversation #%d created for %s (%s)", conversation.id, TEST_PHONE, conversation.business_type)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.error("Error creating conversation: %s", e)
        conversation = None
    finally:
        db.close()

    sid = send_sms(TEST_PHONE, initial_text, from_number=settings.outbound_from)
    print(f"SMS id: {sid}")
    print(f"TEXT SENT: {initial_text}")

    if conversation is not None:
        db = get_session()
        try:
            db.add(Message(
                conversation_id=conversation.id,
                role="assistant",
                content=initial_text,
            ))
            conversation.response_count = 1
            db.commit()
            schedule_follow_up(TEST_PHONE, conversation.id, settings.business_name)
            logger.info("Follow-up scheduled for conv #%d", conversation.id)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            logger.error("Error saving initial SMS: %s", e)
        finally:
            db.close()


if __name__ == "__main__":
    asyncio.run(main())
