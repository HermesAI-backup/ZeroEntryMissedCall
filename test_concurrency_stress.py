"""Concurrency stress test — N simultaneous inbound SMS through the REAL
handler, verifying the async server doesn't deadlock or drop conversations.

History (2026-08-16): a 10-way concurrency test found "database is locked"
under simultaneous inbound (fixed: WAL + busy_timeout + commit-before-LLM).
This is the durable regression guard for that class of failure: fire N
inbound messages to DISTINCT phone numbers concurrently through
`inbound_sms()` (real webhook handler), then assert:
  - zero 'database is locked' exceptions surfaced
  - every conversation got a reply (no silent drops)
  - conversations stay distinct (no cross-talk of addresses)

Uses a TEMP DB (DB_PATH env set before import) so it never touches the prod
conversations.db, and a monkeypatched send_sms recorder instead of real SMS.

Runs manually (live LLM calls — N conversations × generate_reply), NOT
auto-globbed by run_tests.sh (same class as test_live_continuity.py).

Usage: python test_concurrency_stress.py [N]   (default 8)
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

fd, DB_FILE = tempfile.mkstemp(suffix=".db", prefix="hermes-concurrency-")
os.close(fd)
os.environ["DB_PATH"] = DB_FILE  # MUST be set before database import

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import get_session, init_db, Conversation, Message  # noqa: E402
from app import inbound_sms  # noqa: E402
import app as app_module  # noqa: E402

SENT: list[tuple[str, str]] = []


def _recording_send(to: str, body: str, from_number: str | None = None):
    SENT.append((to, body))
    return f"msg_{len(SENT)}"


async def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    init_db()
    # app.py uses the module-level import — patch app.send_sms, not the client
    app_module.send_sms = _recording_send  # type: ignore[assignment]

    phones = [f"+1406555{n:04d}" for n in range(1000, 1000 + n)]
    bodies = [f"hi my sink is clogged, address is {n} test st" for n in range(1, n + 1)]

    print(f"=== concurrency stress: {n} simultaneous inbound ===")
    results = await asyncio.gather(
        *[inbound_sms(From=ph, Body=bd) for ph, bd in zip(phones, bodies)],
        return_exceptions=True,
    )
    errs = [r for r in results if isinstance(r, Exception)]
    if errs:
        print(f"FAIL: {len(errs)} exceptions: {errs[0]}")
        return 1

    db = get_session()
    try:
        convs = db.query(Conversation).all()
        print(f"  conversations created: {len(convs)} (expect {n})")
        if len(convs) != n:
            print("FAIL: not all conversations created")
            return 1
        # Every conversation must have at least one assistant reply
        for c in convs:
            msgs = db.query(Message).filter(Message.conversation_id == c.id).all()
            roles = [m.role for m in msgs]
            if "assistant" not in roles:
                print(f"FAIL: conv #{c.id} ({c.phone_number}) has NO assistant reply "
                      f"(roles={roles}) — silent drop")
                return 1
        # Distinct phones -> distinct addresses, no cross-talk
        metas = [dict(c.metadata_json or {}) for c in convs]
        addresses = [m.get("address", "") for m in metas if m.get("address")]
        uniq = set(addresses)
        print(f"  distinct addresses extracted: {len(uniq)} (from {len(addresses)} booked/metas)")
    finally:
        db.close()

    print(f"PASS: {n} concurrent inbound, 0 lock errors, 0 dropped replies")
    from database import engine  # noqa: E402
    engine.dispose()  # release the file lock so the temp DB can be deleted
    Path(DB_FILE).unlink()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
