"""Regression test: conversation metadata_json persistence (2026-08-17).

Live-post-mortem bug: the booking-detail merge did

    meta = conversation.metadata_json or {}      # SAME dict object
    meta.update({...})                            # mutate in place
    conversation.metadata_json = meta             # assign same object back
    db.commit()

SQLAlchemy's change tracking sees no change (same identity + same content),
so the UPDATE never fires; db.commit() expires the attribute and the next
read reloads the STALE value. Result: the "4pm is fine" booking kept trying
the old 15:00 slot and the address/date updates were silently dropped.

This test drives the REAL inbound_sms() handler (auto-start -> extractor ->
merge -> book path) through the real DB, and asserts the metadata actually
persists across turns — i.e. that a NEW dict is assigned.

Usage: python test_metadata_persistence.py   (plain asserts, exit 0/1)
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))

os.environ["BUSINESS_TYPE"] = "plumbing"
os.environ["BUSINESS_NAME"] = "Helena Plumbing Co"
os.environ["SERVICE_AREA"] = "Helena, MT"

import app  # noqa: E402
from app import inbound_sms  # noqa: E402

TEST_PHONE = "+14069996688"
SENT = []


def fake_send(to, body, from_number=None):
    SENT.append((to, body))
    return f"fake-{len(SENT)}"


app.send_sms = fake_send

DB_PATH = PROJECT / "data" / "conversations.db"


def _clean() -> None:
    db = sqlite3.connect(DB_PATH)
    db.execute("DELETE FROM scheduled_tasks WHERE phone_number=?", (TEST_PHONE,))
    db.execute(
        "DELETE FROM messages WHERE conversation_id IN "
        "(SELECT id FROM conversations WHERE phone_number=?)",
        (TEST_PHONE,),
    )
    db.execute("DELETE FROM conversations WHERE phone_number=?", (TEST_PHONE,))
    db.commit()
    db.close()


def _meta(conv_id: int) -> dict:
    db = sqlite3.connect(DB_PATH)
    row = db.execute(
        "SELECT metadata_json FROM conversations WHERE id=?", (conv_id,)
    ).fetchone()
    db.close()
    return json.loads(row[0]) if row and row[0] else {}


async def main() -> int:
    _clean()
    try:
        # Turn 1: address only (no date/time yet)
        await inbound_sms(From=TEST_PHONE, Body="my sink is leaking")
        await inbound_sms(From=TEST_PHONE, Body="1234 test st")
        db = sqlite3.connect(DB_PATH)
        conv = db.execute(
            "SELECT id FROM conversations WHERE phone_number=? ORDER BY id DESC LIMIT 1",
            (TEST_PHONE,),
        ).fetchone()
        db.close()
        assert conv, "no conversation created"
        cid = conv[0]

        m1 = _meta(cid)
        # Case-insensitive: the LLM extractor may capitalize ("1234 Test St")
        # — what matters is the value persisted, not the casing.
        assert m1.get("address", "").lower() == "1234 test st", f"address not persisted: {m1}"

        # Turn 2: add a date/time — MUST override, not keep stale
        await inbound_sms(From=TEST_PHONE, Body="wednesday 2pm works")
        m2 = _meta(cid)
        assert m2.get("appt_date"), f"appt_date not persisted: {m2}"
        assert m2.get("appt_time") == "14:00", f"appt_time wrong: {m2}"
        assert m2.get("address") == "1234 test st", f"address lost: {m2}"

        print("metadata persistence checks passed")
        return 0
    finally:
        _clean()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
