"""Regression tests: availability-aware scheduling (scheduler.py).

- _first_free_slot: pure slot-walking logic
- next_available_slot: real-calendar-aware next-open-slot proposal
  (tested with synthetic events, no I/O)

2026-08-16: the AI used to confirm bookings without checking availability,
and proposed times with no calendar awareness. These tests guard the new
behavior: conflicts are detected and the next real open slot is offered.

Usage: python test_scheduler_availability.py   (plain asserts, exit 0/1)
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))

from scheduler import _first_free_slot, next_available_slot  # noqa: E402


def _ts(h: int, m: int = 0) -> float:
    """Epoch seconds for a local date-time on 2026-08-17."""
    return datetime.datetime(2026, 8, 17, h, m).timestamp()


def test_first_free_slot_empty_day() -> None:
    s = _first_free_slot([], _ts(8), _ts(18))
    assert s is not None and s.hour == 8


def test_first_free_slot_skips_busy() -> None:
    busy = [(_ts(8), _ts(9)), (_ts(10), _ts(11))]
    s = _first_free_slot(busy, _ts(8), _ts(18))
    assert s is not None and s.hour == 9  # the gap between 9 and 10


def test_first_free_slot_after_pushes_past_busy() -> None:
    busy = [(_ts(8), _ts(10))]
    s = _first_free_slot(busy, _ts(8), _ts(18), after=_ts(8, 30))
    assert s is not None and s.hour == 10


def test_first_free_slot_full_day() -> None:
    busy = [(_ts(8), _ts(18))]
    assert _first_free_slot(busy, _ts(8), _ts(18)) is None


def test_next_available_slot_skips_conflicting_event() -> None:
    events = [
        {"start": {"dateTime": "2026-08-17T09:00:00-06:00"},
         "end": {"dateTime": "2026-08-17T10:00:00-06:00"}},
    ]
    slot = next_available_slot("2026-08-17", "09:00", events=events)
    assert slot == "10:00 AM", slot


def test_next_available_slot_free_day() -> None:
    assert next_available_slot("2026-08-17", "09:00", events=[]) == "9:00 AM"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"PASS: {len(tests)} scheduler-availability checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
