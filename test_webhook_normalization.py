"""Regression test: Telnyx API v2 `from`-object normalization (app._telnym_number).

2026-08-16 production bug: Telnyx v2 sends `from`/`to` as objects
({"phone_number", "carrier", "line_type"}) — both webhook handlers assumed a
plain string, so the first real inbound SMS crashed with
"type 'dict' is not supported" at the conversation lookup.

Side-effect free: no SMS, no DB writes. The live end-to-end proof (real
inbound message → conv #14 → plumbing reply delivered) was done separately.

Usage: python test_webhook_normalization.py   (plain asserts, exit 0/1)
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))

from app import _telnym_number  # noqa: E402

APP_SRC = (PROJECT / "app.py").read_text(encoding="utf-8")


def test_dict_shape() -> None:
    """The exact shape that crashed production."""
    payload_from = {
        "phone_number": "+14064396365",
        "carrier": "AT&T",
        "line_type": "Wireless",
    }
    assert _telnym_number(payload_from) == "+14064396365"


def test_plain_string() -> None:
    assert _telnym_number("+14064396365") == "+14064396365"


def test_empty_and_missing() -> None:
    assert _telnym_number(None) == ""
    assert _telnym_number({}) == ""
    assert _telnym_number({"carrier": "AT&T"}) == ""


def test_wired_into_both_webhooks() -> None:
    """Both the SMS and call webhook routes must normalize before use."""
    assert "frm = _telnym_number(payload.get(\"from\"))" in APP_SRC
    assert "from_number = _telnym_number(payload.get(\"from\"))" in APP_SRC


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"PASS: {len(tests)} webhook-normalization checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
