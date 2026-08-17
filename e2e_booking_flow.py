"""E2E: book-verified scheduling — happy path + conflict fallback, LIVE.

- Conv 14 (Sevin's real phone): customer books "tomorrow at 9am" → real
  calendar event + spreadsheet row + confirmation SMS + owner alert.
- Fake second customer (+14065550002): books the SAME slot → must NOT get a
  false confirmation; gets "just filled up — how about X instead?" fallback.

Runs against the real pipeline (extractor → branch → _book_conversation →
gapi calendar → Telnyx SMS). No mocks.
"""
from __future__ import annotations

import asyncio
import os
import sys
import datetime

os.environ["BUSINESS_TYPE"] = "plumbing"
os.environ["BUSINESS_NAME"] = "Helena Plumbing Co"
os.environ["SERVICE_AREA"] = "Helena, MT"

from app import (  # noqa: E402
    Conversation,
    Message,
    get_session,
    inbound_sms,
    settings,
)

SEVIN = "+14064396365"
OTHER = "+14065550002"


def make_conv(phone: str) -> int:
    """Create an active plumbing conversation (no SMS sent)."""
    from conversation import ConversationEngine
    from database import Contact

    eng = ConversationEngine(business_type="plumbing")
    db = get_session()
    try:
        contact = db.query(Contact).filter(Contact.phone_number == phone).first()
        if not contact:
            contact = Contact(phone_number=phone, tags=["missed-call"])
            db.add(contact)
            db.flush()
        conv = Conversation(
            contact_id=contact.id,
            phone_number=phone,
            business_type="plumbing",
            state="active",
            initial_delay_seconds=0,
            max_responses=eng.max_responses(),
            response_count=1,
            last_ai_sent_at=datetime.datetime.utcnow(),
            metadata_json={},
        )
        db.add(conv)
        db.flush()
        db.add(Message(
            conversation_id=conv.id,
            role="assistant",
            content=eng.get_initial_message(),
        ))
        db.commit()
        return conv.id
    finally:
        db.close()


async def main() -> int:
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    print(f"=== E2E: booking for {tomorrow} 9:00 AM ===")

    print("\n[1] Sevin's conv (#14): 'tomorrow at 9am works for me'")
    await inbound_sms(From=SEVIN, Body="tomorrow at 9am works for me")

    print("\n[2] Second customer books the SAME slot (expect conflict fallback)")
    other_conv = make_conv(OTHER)
    print(f"    created conv #{other_conv} for {OTHER}")
    await inbound_sms(
        From=OTHER,
        Body="hi my sink is clogged, address 456 Oak St, tomorrow at 9am works",
    )

    print("\n=== DB verification ===")
    db = get_session()
    try:
        from sqlite3 import connect
        conn = connect("data/conversations.db")
        c = conn.cursor()
        c.execute(
            "SELECT conversation_id, role, content FROM messages "
            "WHERE conversation_id IN (14, ?) AND role='assistant' ORDER BY id DESC LIMIT 6",
            (other_conv,),
        )
        for row in c.fetchall():
            print(f"    conv {row[0]} [{row[1]}]: {row[2][:90]}")
        c.execute("SELECT id, metadata_json FROM conversations WHERE id IN (14, ?)", (other_conv,))
        for row in c.fetchall():
            meta = row[1] or "{}"
            print(f"    conv {row[0]} metadata: {meta[:120]}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
