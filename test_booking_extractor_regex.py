"""Regression tests: deterministic booking-extraction safety net (2026-08-17).

Live-post-mortem failures this guards:
  1. Customer said "Wednesday August 19th at 3pm works" → LLM extractor
     returned ONLY address (silent empty date/time) → hardcoded fallback
     "I've got everything except the day and time" (the "already told you"
     loop).
  2. Customer said "Afternoon is preferred" then "3pm is fine" → LLM
     anchored on the fuzzy "afternoon" → appt_time=13:00 booked for a 3pm
     request (the 13:00-vs-15:00 disaster).
  3. LLM first pass returns wrong keys (party_size, service_date...) at a
     high rate → correction pass is load-bearing and itself unvalidated.

Guards (pure functions, no LLM, no I/O — deterministic):
  - explicit "3pm"/"15:00" parses to 15:00
  - explicit time BEATS fuzzy "afternoon" (13:00) in the same history
  - "Wednesday" resolves to the NEXT Wednesday
  - "tomorrow" resolves to today+1
  - "August 19th" resolves to YYYY-08-19
  - newest message wins over older mentions

Usage: python test_booking_extractor_regex.py   (plain asserts, exit 0/1)
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))

from conversation import _regex_extract_booking  # noqa: E402


def hist(*msgs: str) -> list[dict]:
    return [{"role": "user", "content": m} for m in msgs]


def test_explicit_3pm() -> None:
    r = _regex_extract_booking(hist("Wednesday August 19th at 3pm works"))
    assert r["appt_time"] == "15:00", r
    assert r["_explicit_time"] is True


def test_explicit_3pm_beats_fuzzy_afternoon() -> None:
    # The disaster: customer said "Afternoon is preferred" earlier, "3pm" last.
    r = _regex_extract_booking(hist("Afternoon is preferred", "Actually 3pm is fine"))
    assert r["appt_time"] == "15:00", f"fuzzy afternoon (13:00) must NOT win: {r}"


def test_fuzzy_afternoon_when_no_explicit() -> None:
    r = _regex_extract_booking(hist("Afternoon is preferred"))
    assert r["appt_time"] == "13:00", r
    assert r["_explicit_time"] is False


def test_24h_time() -> None:
    r = _regex_extract_booking(hist("book me at 15:00"))
    assert r["appt_time"] == "15:00", r
    assert r["_explicit_time"] is True


def test_12h_with_minutes() -> None:
    r = _regex_extract_booking(hist("3:30 pm works"))
    assert r["appt_time"] == "15:30", r


def test_wednesday_next_week() -> None:
    today = datetime.date.today()
    r = _regex_extract_booking(hist("wednesday works"))
    parsed = datetime.date.fromisoformat(r["appt_date"])
    assert parsed.weekday() == 2, f"should be a Wednesday: {r}"
    assert parsed > today, f"must be in the future: {r}"
    assert (parsed - today).days <= 7, f"should be next Wednesday at most: {r}"


def test_weekday_abbreviations() -> None:
    today = datetime.date.today()
    for abbr, wd in [("mon", 0), ("tue", 1), ("wed", 2), ("thu", 3), ("fri", 4), ("sat", 5), ("sun", 6)]:
        r = _regex_extract_booking(hist(f"{abbr} works"))
        assert r["appt_date"], f"{abbr} should resolve a date: {r}"
        parsed = datetime.date.fromisoformat(r["appt_date"])
        assert parsed.weekday() == wd, f"{abbr} -> {parsed} weekday {parsed.weekday()}, want {wd}"
        assert parsed > today, f"{abbr} must be future: {parsed}"


def test_tomorrow() -> None:
    today = datetime.date.today()
    r = _regex_extract_booking(hist("tomorrow morning"))
    assert r["appt_date"] == (today + datetime.timedelta(days=1)).isoformat(), r
    assert r["appt_time"] == "09:00", r  # fuzzy morning


def test_month_day() -> None:
    today = datetime.date.today()
    r = _regex_extract_booking(hist("August 19th works"))
    expected = f"{today.year}-08-19"
    assert r["appt_date"] == expected, f"{r} != {expected}"


def test_newest_message_wins() -> None:
    r = _regex_extract_booking(hist("tomorrow", "actually make it friday"))
    parsed = datetime.date.fromisoformat(r["appt_date"])
    assert parsed.weekday() == 4, f"friday should win: {r}"


def test_combined_explicit_date_and_time() -> None:
    r = _regex_extract_booking(hist("Wednesday August 19th at 3pm works, name is Test Customer"))
    assert r["appt_date"] == f"{datetime.date.today().year}-08-19", r
    assert r["appt_time"] == "15:00", r


def test_no_date_no_time() -> None:
    r = _regex_extract_booking(hist("my sink is clogged"))
    assert r["appt_date"] == "" and r["appt_time"] == "", r


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\nPASS: {len(tests) - failed}/{len(tests)} extractor-regex checks")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
