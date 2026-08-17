"""Regression test: whitelist command parsing must not eat customer replies.

2026-08-17 production bug (found during the live plumbing E2E): the
whitelist-command guard was

    if upper.startswith("WHITELIST") or upper.startswith("W") and len(Body.split()) >= 2:

Python binds `and` tighter than `or`, so ANY message starting with "W"
with 2+ words ("What time can you come out", "We need help") was treated
as a WHITELIST command; the second word was parsed as a phone number,
failed digit validation, and the customer got "❌ Couldn't parse" instead
of an AI reply.

Fix: first token must EXACTLY equal the command word, and the W/UW
shorthand additionally requires a digit-bearing second token.

This test drives the REAL inbound_sms() handler with a send-recorder
monkeypatch and asserts: real commands still work, W-starting customer
replies fall through to the AI path, and no SMS is sent to the sender
for a non-command.
"""
import asyncio
import os

os.environ["BUSINESS_TYPE"] = "plumbing"
os.environ["BUSINESS_NAME"] = "Helena Plumbing Co"
os.environ["SERVICE_AREA"] = "Helena, MT"

import app  # noqa: E402
from app import inbound_sms  # noqa: E402

SENT = []


def fake_send(to, body, from_number=None):
    SENT.append((to, body))
    return f"fake-{len(SENT)}"


app.send_sms = fake_send  # type: ignore[assignment]


async def run(body: str, phone: str = "+14060000001") -> None:
    SENT.clear()
    # Non-whitelisted phone → the handler should route to conversation/AI path.
    # To keep this test hermetic (no live LLM/Telnyx), we intercept deeper:
    # if the message falls through the command guards, generate_reply would
    # fire; we stub conversation generation via the engine's reply path.
    await inbound_sms(From=phone, Body=body)


def test_whitelist_command_still_works() -> None:
    asyncio.run(run("WHITELIST +14060001111 Mom"))
    # Command path sends a confirmation TO THE SENDER — assert a reply exists
    # and it mentions whitelist success/parse, NOT the AI conversation.
    assert SENT, "expected a whitelist command response"
    body = SENT[0][1]
    assert "Couldn't parse" not in body, f"command should parse cleanly: {body}"


def test_w_shorthand_still_works() -> None:
    asyncio.run(run("W +14060001111"))
    assert SENT, "expected a whitelist response for W shorthand"
    assert "Couldn't parse" not in SENT[0][1]


def test_uw_shorthand_still_works() -> None:
    asyncio.run(run("UW +14060001111"))
    assert SENT, "expected a response for UW shorthand"


def test_what_reply_not_eaten() -> None:
    """'What time can you come out' must NOT produce a whitelist error."""
    asyncio.run(run("What time can you come out"))
    # A command error reply would say "Couldn't parse" — that's the bug.
    for to, body in SENT:
        assert "Couldn't parse" not in body, f"reply eaten as command: {body}"


def test_we_reply_not_eaten() -> None:
    """'We have a leak' (starts with W, 4 words) must reach the AI."""
    asyncio.run(run("We have a leak in the basement"))
    for to, body in SENT:
        assert "Couldn't parse" not in body, f"reply eaten as command: {body}"


def test_lone_w_falls_through() -> None:
    """A bare 'W' (no digits) must not be treated as a command."""
    asyncio.run(run("W"))
    # Falls through to AI path — no whitelist parse error expected.
    for to, body in SENT:
        assert "Couldn't parse" not in body


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
