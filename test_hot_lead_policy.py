"""Regression test: hot-lead owner-alert policy (Sevin directive, Aug 16).

Clients must NOT receive hot-lead SMS — only emergencies get an immediate
owner alert (bookings alert separately). Hot-lead owner alerts are for the
sales/dogfood persona only; client personas log + tag and surface in the
daily summary.

Usage: python test_hot_lead_policy.py   (plain asserts, exit 0/1)
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))

from app import _hot_lead_alerts_owner  # noqa: E402

APP_SRC = (PROJECT / "app.py").read_text(encoding="utf-8")


def test_sales_persona_alerts() -> None:
    assert _hot_lead_alerts_owner("sales") is True


def test_client_personas_do_not_alert() -> None:
    for persona in ("plumbing", "hvac", "septic", "default"):
        assert _hot_lead_alerts_owner(persona) is False, persona


def test_guard_wired_into_branch_action() -> None:
    assert "if _hot_lead_alerts_owner(conversation.business_type):" in APP_SRC
    assert "no owner alert (logged, daily summary)" in APP_SRC


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"PASS: {len(tests)} hot-lead policy checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
