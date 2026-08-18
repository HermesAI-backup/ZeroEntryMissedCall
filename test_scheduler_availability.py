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

from scheduler import (  # noqa: E402
    _first_free_slot,
    _last_free_slot,
    availability_summary,
    nearest_available_slots,
    next_available_slot,
)


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


def test_last_free_slot_empty_day() -> None:
    s = _last_free_slot([], _ts(8), _ts(18))
    assert s is not None and s.hour == 17  # latest slot ending at day_end


def test_last_free_slot_before_requested_time() -> None:
    # 2pm requested (14:00); busy 9-10. Latest slot ending at/before 14:00 = 13:00.
    busy = [(_ts(9), _ts(10))]
    s = _last_free_slot(busy, _ts(8), _ts(18), before=_ts(14))
    assert s is not None and s.hour == 13, s


def test_last_free_slot_skips_busy_after() -> None:
    # busy 13-14: latest slot ending at/before 14:00 must NOT overlap 13-14.
    busy = [(_ts(13), _ts(14))]
    s = _last_free_slot(busy, _ts(8), _ts(18), before=_ts(14))
    assert s is not None and s.hour == 12, s


def test_nearest_available_slots_before_and_after() -> None:
    # 2pm requested; busy 14-15 (the requested slot itself) and 16-17.
    # Closest before = 1:00 PM; closest after = 3:00 PM (15-16 is free),
    # not 5:00 PM.
    events = [
        {"start": {"dateTime": "2026-08-17T14:00:00-06:00"},
         "end": {"dateTime": "2026-08-17T15:00:00-06:00"}},
        {"start": {"dateTime": "2026-08-17T16:00:00-06:00"},
         "end": {"dateTime": "2026-08-17T17:00:00-06:00"}},
    ]
    nxt = nearest_available_slots("2026-08-17", "14:00", events=events)
    assert nxt["before"] == "1:00 PM", nxt
    assert nxt["after"] == "3:00 PM", nxt


def test_nearest_available_slots_before_only() -> None:
    # 8am requested on a day fully booked 9-18: only a before slot at 8:00 AM.
    events = [
        {"start": {"dateTime": "2026-08-17T09:00:00-06:00"},
         "end": {"dateTime": "2026-08-17T18:00:00-06:00"}},
    ]
    nxt = nearest_available_slots("2026-08-17", "10:00", events=events)
    assert nxt["before"] == "8:00 AM", nxt
    assert nxt["after"] is None, nxt


def test_nearest_available_slots_after_only() -> None:
    # 15:00 requested on a day fully booked 8-16: no before slot
    # (earliest = 8:00 but that's busy), closest after = 4:00 PM (16-17).
    events = [
        {"start": {"dateTime": "2026-08-17T08:00:00-06:00"},
         "end": {"dateTime": "2026-08-17T16:00:00-06:00"}},
    ]
    nxt = nearest_available_slots("2026-08-17", "15:00", events=events)
    assert nxt["before"] is None, nxt
    assert nxt["after"] == "4:00 PM", nxt


def test_availability_summary_lists_taken_and_free() -> None:
    events = [
        {"start": {"dateTime": "2026-08-17T14:00:00-06:00"},
         "end": {"dateTime": "2026-08-17T15:00:00-06:00"}},
    ]
    summary = availability_summary("2026-08-17", events=events)
    assert "TAKEN 2:00 PM" in summary, summary
    assert "1:00 PM" in summary, summary   # free slot right before
    assert "3:00 PM" in summary, summary   # free slot right after


def test_availability_summary_invalid_date() -> None:
    assert availability_summary("not-a-date") == ""


def test_availability_summary_fully_booked() -> None:
    events = [
        {"start": {"dateTime": "2026-08-17T08:00:00-06:00"},
         "end": {"dateTime": "2026-08-17T18:00:00-06:00"}},
    ]
    summary = availability_summary("2026-08-17", events=events)
    assert "TAKEN 8:00 AM" in summary, summary
    assert "fully booked" in summary or "FREE none" in summary, summary


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"PASS: {len(tests)} scheduler-availability checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
