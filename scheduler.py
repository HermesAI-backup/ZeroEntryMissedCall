"""Scheduling engine — Google Calendar + Sheets for appointment booking."""

from __future__ import annotations

import datetime
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("scheduler")

SPREADSHEET_ID = "125kdPgT4YS8D2eRZHXA8Y3V0qVDlZVjtkk4Bou-rTIY"
SHEET_NAME = "Schedule"


def _find_gapi_script() -> str:
    """Locate the google-workspace skill's google_api.py across layouts.

    The skill lives under the ACTIVE Hermes profile's skills dir; the profile
    was renamed (email-auto → textback-ai), which moved the path. Try the
    current profile, any profile, and legacy locations before giving up.
    """
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates: list[Path] = [
        local / "hermes" / "profiles" / "textback-ai" / "skills" / "productivity"
        / "google-workspace" / "scripts" / "google_api.py",
    ]
    profiles_dir = local / "hermes" / "profiles"
    if profiles_dir.exists():
        candidates += [
            p / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py"
            for p in sorted(profiles_dir.glob("*"))
        ]
    candidates += [
        Path(__file__).resolve().parent.parent / "skills" / "productivity"
        / "google-workspace" / "scripts" / "google_api.py",
        Path.home() / ".hermes" / "skills" / "productivity"
        / "google-workspace" / "scripts" / "google_api.py",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return str(candidates[0])  # best effort — _run_gapi will surface the error


GAPI_SCRIPT = _find_gapi_script()


def _run_gapi(*args: str) -> dict:
    """Run a google_api.py command and return parsed JSON."""
    cmd = ["python", GAPI_SCRIPT] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            logger.error("gapi error: %s", result.stderr[:500])
            return {"status": "error", "error": result.stderr[:500]}
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        logger.error("gapi timed out")
        return {"status": "error", "error": "timeout"}
    except json.JSONDecodeError:
        logger.error("gapi returned non-JSON: %s", result.stdout[:200])
        return {"status": "error", "error": "non-JSON response"}


def _event_bounds(event: dict) -> tuple[str, str]:
    """Extract (start, end) ISO strings from a calendar event.

    google_api.py returns FLATTENED events ("start": "2026-08-18T02:00:00Z");
    the raw Google API nests them ({"dateTime": ...}). Handle both.
    """
    s = event.get("start", {})
    e = event.get("end", {})
    s_raw = s.get("dateTime", "") if isinstance(s, dict) else s
    e_raw = e.get("dateTime", "") if isinstance(e, dict) else e
    return str(s_raw or ""), str(e_raw or "")


def check_availability(
    date_str: str, time_str: str, duration_minutes: int = 60
) -> tuple[bool, str]:
    """Check if a time slot is available on Google Calendar.
    
    Args:
        date_str: "2026-07-30"
        time_str: "10:00 AM" or "10:00"
        duration_minutes: how long the appointment is
    
    Returns:
        (is_available, message)
    """
    # Parse time
    try:
        if "AM" in time_str.upper() or "PM" in time_str.upper():
            dt = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I:%M %p")
        else:
            dt = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return False, "Couldn't understand that time. Can you try a different format?"
    
    # Build ISO start/end with MT timezone (Helena = UTC-6/-7)
    tz = "-06:00"  # Montana is MDT (UTC-6) in summer
    start_str = dt.strftime(f"%Y-%m-%dT%H:%M:%S{tz}")
    end_dt = dt + datetime.timedelta(minutes=duration_minutes)
    end_str = end_dt.strftime(f"%Y-%m-%dT%H:%M:%S{tz}")
    
    # List events on that day
    day_start = dt.replace(hour=0, minute=0).strftime(f"%Y-%m-%dT%H:%M:%S{tz}")
    day_end = dt.replace(hour=23, minute=59).strftime(f"%Y-%m-%dT%H:%M:%S{tz}")
    
    result = _run_gapi("calendar", "list", "--start", day_start, "--end", day_end)

    if isinstance(result, dict) and result.get("status") == "error":
        logger.error("Calendar check failed: %s", result.get("error"))
        return True, ""  # If calendar fails, let it through
    
    events = result if isinstance(result, list) else result.get("data", [])
    
    # Check for overlaps
    slot_start = dt.timestamp()
    slot_end = (dt + datetime.timedelta(minutes=duration_minutes)).timestamp()
    
    for event in events:
        event_start, event_end = _event_bounds(event)
        if event_start and event_end:
            try:
                es = datetime.datetime.fromisoformat(event_start).timestamp()
                ee = datetime.datetime.fromisoformat(event_end).timestamp()
                # Overlap if: existing starts before our end AND existing ends after our start
                if es < slot_end and ee > slot_start:
                    return False, f"That time conflicts with {event.get('summary', 'an existing appointment')}. Any other time work for you?"
            except (ValueError, TypeError):
                continue
    
    return True, ""


def _first_free_slot(
    busy_ranges: list[tuple[float, float]],
    day_start: float,
    day_end: float,
    duration_minutes: int = 60,
    after: float | None = None,
) -> datetime.datetime | None:
    """Find the first free slot of duration_minutes within [day_start, day_end),
    at/after `after`. busy_ranges are [start, end) epoch timestamps.

    Pure function — no I/O — so it's unit-testable.
    """
    cursor = max(day_start, after or day_start)
    slot_seconds = duration_minutes * 60
    for es, ee in sorted(busy_ranges):
        if ee <= cursor:
            continue
        if es - cursor >= slot_seconds:
            return datetime.datetime.fromtimestamp(cursor)
        cursor = max(cursor, ee)
    if day_end - cursor >= slot_seconds:
        return datetime.datetime.fromtimestamp(cursor)
    return None


def next_available_slot(
    date_str: str,
    time_str: str = "",
    duration_minutes: int = 60,
    day_start_hour: int = 8,
    day_end_hour: int = 18,
    events: list | None = None,
) -> str | None:
    """Find the next open slot on the requested day, at/after the requested time.

    Uses REAL calendar data (existing events on that day). Default window
    8am-6pm — no per-client business hours exist yet; BUSINESS_HOURS will
    override this later (see Master Task List §3). Returns "3:00 PM" or None.

    Pass `events` (list of gapi calendar events) to test without I/O.
    """
    try:
        if time_str and ("AM" in time_str.upper() or "PM" in time_str.upper()):
            dt = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I:%M %p")
        elif time_str:
            dt = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        else:
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(hour=day_start_hour)
    except ValueError:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(hour=day_start_hour)

    tz = "-06:00"
    day_start = dt.replace(hour=day_start_hour, minute=0)
    day_end = dt.replace(hour=day_end_hour, minute=0)

    if events is None:
        result = _run_gapi(
            "calendar", "list",
            "--start", day_start.strftime(f"%Y-%m-%dT%H:%M:%S{tz}"),
            "--end", day_end.strftime(f"%Y-%m-%dT%H:%M:%S{tz}"),
        )
        if isinstance(result, dict) and result.get("status") == "error":
            logger.error("Calendar check failed: %s", result.get("error"))
            return None
        events = result if isinstance(result, list) else result.get("data", [])

    busy = []
    for event in events:
        es_raw, ee_raw = _event_bounds(event)
        if es_raw and ee_raw:
            try:
                busy.append((
                    datetime.datetime.fromisoformat(es_raw).timestamp(),
                    datetime.datetime.fromisoformat(ee_raw).timestamp(),
                ))
            except (ValueError, TypeError):
                continue

    slot = _first_free_slot(
        busy,
        day_start.timestamp(),
        day_end.timestamp(),
        duration_minutes,
        after=dt.timestamp() if time_str else None,
    )
    if slot is None:
        return None
    return slot.strftime("%I:%M %p").lstrip("0")


def book_appointment(
    date_str: str,
    time_str: str,
    customer_name: str,
    customer_phone: str,
    address: str,
    service_type: str,
    business_name: str = "Your Business",
    duration_minutes: int = 60,
) -> dict:
    """Book an appointment: check availability, create calendar event, log to sheet.
    
    Returns dict with status and details.
    """
    # 1. Check availability
    available, msg = check_availability(date_str, time_str, duration_minutes)
    if not available:
        return {"status": "conflict", "message": msg}
    
    # 2. Parse time for calendar
    try:
        if "AM" in time_str.upper() or "PM" in time_str.upper():
            dt = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I:%M %p")
        else:
            dt = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return {"status": "error", "message": "Couldn't parse time."}
    
    tz = "-06:00"
    start_str = dt.strftime(f"%Y-%m-%dT%H:%M:%S{tz}")
    end_dt = dt + datetime.timedelta(minutes=duration_minutes)
    end_str = end_dt.strftime(f"%Y-%m-%dT%H:%M:%S{tz}")
    
    summary = f"{service_type} - {customer_name}"
    description = (
        f"Customer: {customer_name}\n"
        f"Phone: {customer_phone}\n"
        f"Address: {address}\n"
        f"Service: {service_type}\n"
        f"Business: {business_name}"
    )
    
    # 3. Create calendar event
    cal_result = _run_gapi(
        "calendar", "create",
        "--summary", summary,
        "--start", start_str,
        "--end", end_str,
        "--description", description,
    )
    
    if cal_result.get("status") == "error":
        logger.error("Calendar create failed: %s", cal_result.get("error"))
        return {"status": "error", "message": "Couldn't book the appointment. Please try again."}
    
    event_id = cal_result.get("id", "unknown")
    event_link = cal_result.get("htmlLink", "")
    
    # 4. Log to spreadsheet
    now = datetime.datetime.now().strftime("%Y-%m-%d")
    time_display = dt.strftime("%I:%M %p").lstrip("0")
    
    sheet_result = _run_gapi(
        "sheets", "append", SPREADSHEET_ID, f"{SHEET_NAME}!A:I",
        "--values",
        json.dumps([[date_str, time_display, customer_name, customer_phone, address, service_type, business_name, "Confirmed", f"Cal: {event_id}"]]),
    )
    
    if sheet_result.get("status") == "error":
        logger.warning("Sheet append failed: %s", sheet_result.get("error"))
    
    return {
        "status": "booked",
        "message": f"Perfect! Appointed {time_display} on {date_str}. We'll send a reminder.",
        "event_id": event_id,
        "date": date_str,
        "time": time_display,
    }
