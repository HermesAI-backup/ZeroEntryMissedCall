"""Scheduling engine — Google Calendar + Sheets for appointment booking."""

from __future__ import annotations

import datetime
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("scheduler")

SPREADSHEET_ID = "125kdPgT4YS8D2eRZHXA8Y3V0qVDlZVjtkk4Bou-rTIY"
SHEET_NAME = "Schedule"

GAPI_SCRIPT = str(
    Path(__file__).resolve().parent.parent
    / "skills"
    / "productivity"
    / "google-workspace"
    / "scripts"
    / "google_api.py"
)


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
    
    if result.get("status") == "error":
        logger.error("Calendar check failed: %s", result.get("error"))
        return True, ""  # If calendar fails, let it through
    
    events = result if isinstance(result, list) else result.get("data", [])
    
    # Check for overlaps
    slot_start = dt.timestamp()
    slot_end = (dt + datetime.timedelta(minutes=duration_minutes)).timestamp()
    
    for event in events:
        event_start = event.get("start", {}).get("dateTime", "")
        event_end = event.get("end", {}).get("dateTime", "")
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
