"""Regression test: webhook retry dedupe + graceful max-responses close.

2026-08-16 production bug: Telnyx webhook retries (of messages sent while the
webhook URL was dead) re-entered conversations, burned the response counter,
and pushed conversations to max_responses — after which inbound messages were
silently dropped ("hit max responses — marking completed"). Customers got zero
reply.

Fixes under test:
  - _is_duplicate_webhook(): drop retried message.received by Telnyx msg id
  - inbound max-responses path sends a closing text instead of silence

Side-effect free: no SMS, no DB writes.

Usage: python test_inbound_retry_dedupe.py   (plain asserts, exit 0/1)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))

import app  # noqa: E402

APP_SRC = (PROJECT / "app.py").read_text(encoding="utf-8")


def test_first_seen_is_not_duplicate() -> None:
    assert app._is_duplicate_webhook("msg-1") is False


def test_repeat_is_duplicate() -> None:
    assert app._is_duplicate_webhook("msg-1") is True  # seen in previous test


def test_window_expiry() -> None:
    app._WEBHOOK_MSG_IDS["stale-msg"] = time.time() - (app._WEBHOOK_DEDUPE_WINDOW + 10)
    # Stale entry is pruned, so the id is treated as new (False = not duplicate)
    assert app._is_duplicate_webhook("stale-msg") is False
    # ...and its timestamp was refreshed to now
    assert time.time() - app._WEBHOOK_MSG_IDS["stale-msg"] < 5


def test_dedupe_wired_into_webhook() -> None:
    assert "_is_duplicate_webhook(msg_id)" in APP_SRC


def test_graceful_close_wired() -> None:
    assert "sending closing message" in APP_SRC
    assert "someone will follow up soon" in APP_SRC


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"PASS: {len(tests)} inbound-reliability checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
