"""LLM-powered conversation engine — generates replies, manages state."""

from __future__ import annotations

import datetime
import json
import logging
import re
from zoneinfo import ZoneInfo

import yaml
from pathlib import Path

logger = logging.getLogger("conversation")

from config import get_settings
from llm_client import LLMClient
from branching import evaluate_branch, BranchName

settings = get_settings()


# ----------------------------------------------------------------------
# Deterministic booking-detail extraction (2026-08-17).
#
# The LLM extractor is the primary path but is NOT reliable on its own:
# it frequently returns wrong keys (party_size, service_date, phone...)
# or silently returns empty date/time despite the customer stating them.
# This regex layer is the safety net — it pulls EXPLICIT times/dates from
# the raw text and enforces precedence:
#   * explicit "3pm" / "15:00" BEATS fuzzy "afternoon" (which the prompt
#     maps to 13:00) — the disaster booked 13:00 for a 3pm request
#   * newest message wins over older mentions
# ----------------------------------------------------------------------

_EXPLICIT_TIME_RE = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)", re.IGNORECASE
)
_24H_TIME_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
_FUZZY_TIME_RE = re.compile(
    r"\b(morning|afternoon|evening|noon|midnight)\b", re.IGNORECASE
)
_MONTH_DAY_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_RELATIVE_DATE_RE = re.compile(r"\b(today|tomorrow)\b", re.IGNORECASE)
_WEEKDAY_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_FUZZY_TIMES = {
    "morning": "09:00", "afternoon": "13:00", "evening": "17:00",
    "noon": "12:00", "midnight": "00:00",
}


def _regex_extract_booking(
    conversation_history: list[dict],
) -> dict[str, str]:
    """Deterministic extraction of appt_date/appt_time from the raw text.

    Returns {"appt_date": ..., "appt_time": ..., "_explicit_time": bool}.
    Newest customer message wins. Explicit clock times beat fuzzy words.
    """
    today = datetime.date.today()
    # Customer messages, newest first
    customer_msgs = [
        m["content"] for m in conversation_history
        if m.get("role") == "user"
    ][::-1]
    text_all = " ".join(customer_msgs)

    appt_date = ""
    appt_time = ""
    explicit_time = False

    for msg in customer_msgs:
        if not appt_time:
            m = _EXPLICIT_TIME_RE.search(msg)
            if m:
                hour, minute, mer = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
                if "p" in mer and hour < 12:
                    hour += 12
                elif "a" in mer and hour == 12:
                    hour = 0
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    appt_time = f"{hour:02d}:{minute:02d}"
                    explicit_time = True
            if not appt_time:
                m = _24H_TIME_RE.search(msg)
                if m:
                    hour, minute = int(m.group(1)), int(m.group(2))
                    if 0 <= hour <= 23 and 0 <= minute <= 59:
                        appt_time = f"{hour:02d}:{minute:02d}"
                        explicit_time = True
            if not appt_time:
                m = _FUZZY_TIME_RE.search(msg)
                if m:
                    appt_time = _FUZZY_TIMES[m.group(1).lower()]

        if not appt_date:
            m = _MONTH_DAY_RE.search(msg)
            if m:
                month, day = _MONTHS[m.group(1).lower()], int(m.group(2))
                try:
                    appt_date = datetime.date(today.year, month, day).isoformat()
                except ValueError:
                    appt_date = ""
            if not appt_date:
                m = _NUMERIC_DATE_RE.search(msg)
                if m:
                    month, day = int(m.group(1)), int(m.group(2))
                    year = int(m.group(3)) if m.group(3) else today.year
                    if year < 100:
                        year += 2000
                    try:
                        appt_date = datetime.date(year, month, day).isoformat()
                    except ValueError:
                        appt_date = ""
            if not appt_date:
                m = _RELATIVE_DATE_RE.search(msg)
                if m:
                    delta = 1 if m.group(1).lower() == "tomorrow" else 0
                    appt_date = (today + datetime.timedelta(days=delta)).isoformat()
            if not appt_date:
                m = _WEEKDAY_RE.search(msg)
                if m:
                    target = _WEEKDAYS[m.group(1).lower()]
                    days_ahead = (target - today.weekday()) % 7
                    if days_ahead == 0:
                        days_ahead = 7  # "wednesday" = NEXT wednesday, not today
                    appt_date = (today + datetime.timedelta(days=days_ahead)).isoformat()

        if appt_date and appt_time:
            break

    return {"appt_date": appt_date, "appt_time": appt_time, "_explicit_time": explicit_time}


class ConversationEngine:
    """Manages AI conversation logic for a specific business type."""

    def __init__(self, business_type: str | None = None):
        self.business_type = business_type or settings.business_type
        self.prompt = self._load_prompt()
        self.llm = LLMClient()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_initial_message(self) -> str:
        """Return the first SMS sent after a missed call."""
        return self._render(self.prompt.get("initial_message", ""))

    def max_responses(self) -> int:
        return int(self.prompt.get("max_ai_responses", 5))

    def delay_seconds(self) -> int:
        return int(self.prompt.get("response_delay_seconds", 35))

    def review_link(self) -> str:
        """Return this business's review link (from prompt YAML, else global env)."""
        return str(self.prompt.get("review_link", "") or "")

    async def generate_reply(
        self,
        conversation_history: list[dict],
    ) -> tuple[str, BranchName, str | None, dict[str, str]]:
        """Generate the next AI reply, evaluate which branch we hit,
        and extract any booking details gathered so far.

        Returns (reply_text, branch, branch_reason, booking_details).
        booking_details is a dict with keys:
          customer_name, address, appt_date, appt_time
        (empty strings for fields not yet collected).
        """
        system_prompt = self._build_system_prompt()
        messages = [{"role": "system", "content": system_prompt}]
        for msg in conversation_history:
            messages.append({"role": msg["role"], "content": msg["content"]})

        reply = await self.llm.chat(messages=messages, temperature=0.7, max_tokens=600)

        # Check if we've hit a branch
        all_messages = conversation_history + [{"role": "assistant", "content": reply}]
        branch, reason = await evaluate_branch(all_messages, self.business_type)

        # Extract structured booking details from the full conversation
        booking_details = await self.extract_booking_details(all_messages)

        return reply, branch, reason, booking_details

    async def extract_booking_details(
        self,
        conversation_history: list[dict],
    ) -> dict[str, str]:
        """Parse structured booking info from conversation history.

        Uses the LLM to extract:
          - customer_name  — the customer's full name
          - address        — the service address
          - appt_date      — the appointment date (YYYY-MM-DD)
          - appt_time      — the appointment time (HH:MM)

        Returns a dict with all four keys; any field not yet mentioned
        by the customer is returned as an empty string.
        """
        schema = {
            "type": "object",
            "properties": {
                "customer_name": {
                    "type": "string",
                    "description": "The customer's full name, or empty string if not yet provided",
                },
                "address": {
                    "type": "string",
                    "description": "The service address, or empty string if not yet provided",
                },
                "appt_date": {
                    "type": "string",
                    "description": "The appointment date in YYYY-MM-DD format, or empty string",
                },
                "appt_time": {
                    "type": "string",
                    "description": "The appointment time in HH:MM format, or empty string",
                },
            },
            "required": ["customer_name", "address", "appt_date", "appt_time"],
        }

        # Anchor relative dates ("tomorrow", "next Tuesday") to TODAY. Without
        # this the extractor can't turn "tomorrow morning" into a real date.
        today = datetime.datetime.now(ZoneInfo("America/Denver")).date().isoformat()
        system_prompt = (
            "You are a booking-detail extractor. Read the conversation below and "
            "extract the customer's booking information. Return ONLY valid JSON with "
            "the exact keys specified. Use an empty string for any field the customer "
            "has NOT yet provided.\n"
            f"Today's date is {today}. Interpret relative dates ('tomorrow', "
            "'next Tuesday', 'this week', 'next week') as actual dates relative to "
            "today, and relative times ('morning' = 09:00, 'afternoon' = 13:00, "
            "'evening' = 17:00) as concrete HH:MM times."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            *conversation_history,
        ]

        result = await self.llm.chat_structured(
            messages=messages,
            json_schema=schema,
            temperature=0.1,
        )

        # Some models (deepseek reasoning, gpt-4.1-mini) return the WRONG keys
        # ("date"/"time"/"name") despite the schema. One correction pass with
        # the exact shape spelled out fixes it deterministically — but the
        # correction result itself is NOT validated, and if it also fails the
        # empties flow through silently (2026-08-17). Retry up to 2 times.
        required = ["customer_name", "address", "appt_date", "appt_time"]
        for attempt in range(3):
            if isinstance(result, dict) and all(k in result for k in required):
                break
            logger.warning(
                "Extractor returned wrong shape (%s) — correction pass %d/3",
                list(result.keys()) if isinstance(result, dict) else type(result).__name__,
                attempt + 1,
            )
            correction = [
                {"role": "system", "content": system_prompt},
                *conversation_history,
                {
                    "role": "assistant",
                    "content": json.dumps(result) if isinstance(result, dict) else str(result),
                },
                {
                    "role": "user",
                    "content": (
                        "That response used the wrong keys. Return the booking info as "
                        "JSON with EXACTLY these keys and nothing else: "
                        '{"customer_name": "full name or empty string", '
                        '"address": "service address or empty string", '
                        '"appt_date": "YYYY-MM-DD or empty string", '
                        '"appt_time": "HH:MM or empty string"}. '
                        "Use empty strings for any field the customer has NOT provided."
                    ),
                },
            ]
            result = await self.llm.chat_structured(
                correction, json_schema=schema, temperature=0.1
            )

        if not isinstance(result, dict) or any(k not in result for k in required):
            logger.warning(
                "Extractor still wrong shape after corrections (%s) — regex fallback will fill what it can",
                list(result.keys()) if isinstance(result, dict) else type(result).__name__,
            )
            result = {}

        # Defensive: ensure all keys exist, fill missing with ""
        normalized = {
            "customer_name": result.get("customer_name", ""),
            "address": result.get("address", ""),
            "appt_date": result.get("appt_date", ""),
            "appt_time": result.get("appt_time", ""),
        }

        # Deterministic safety net (2026-08-17): the LLM extractor is flaky —
        # wrong keys, silent empties, fuzzy-time anchoring. Overlay the regex
        # extraction with precedence:
        #   * appt_time: explicit "3pm"/"15:00" ALWAYS beats an LLM fuzzy
        #     value (e.g. 13:00 for "afternoon") — the disaster booked 13:00
        #     for a 3pm request. If the LLM has no time but the text does,
        #     fill it.
        #   * appt_date: fill empty with the regex date; if both present,
        #     trust the LLM (it understands "this week" context better).
        regex = _regex_extract_booking(conversation_history)
        if regex["appt_time"]:
            if regex["_explicit_time"]:
                normalized["appt_time"] = regex["appt_time"]
            elif not normalized["appt_time"]:
                normalized["appt_time"] = regex["appt_time"]
        if regex["appt_date"] and not normalized["appt_date"]:
            normalized["appt_date"] = regex["appt_date"]

        return normalized

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_prompt(self) -> dict:
        prompt_file = (
            settings.prompt_dir / f"{self.business_type}.yaml"
        )
        if not prompt_file.exists():
            prompt_file = settings.prompt_dir / "default.yaml"

        with open(prompt_file, "r") as f:
            raw = yaml.safe_load(f)
        return raw or {}

    def _build_system_prompt(self) -> str:
        """Assemble the full system prompt from the YAML template + config."""
        identity = self._render(self.prompt.get("identity", ""))
        objectives = self._render(self.prompt.get("objectives", ""))
        rules = self.prompt.get("rules", [])
        rules_str = "\n".join(f"- {r}" for r in rules) if isinstance(rules, list) else rules

        flow = self.prompt.get("conversation_flow", [])
        flow_str = "\n".join(f"{i+1}. {step}" for i, step in enumerate(flow)) if isinstance(flow, list) else ""

        parts = [
            identity,
            "",
            "OBJECTIVES:",
            objectives,
            "",
            "RULES:",
            rules_str,
        ]
        if flow_str:
            parts.extend(["", "CONVERSATION FLOW:", flow_str])

        return "\n".join(parts)

    def _render(self, text: str) -> str:
        """Render {{variables}} from config."""
        replacements = {
            "business_name": settings.business_name,
            "business_type": settings.business_type.replace("-", " ").title(),
            "service_area": settings.service_area,
            "service_description": self.business_type.replace("-", " "),
        }
        for key, val in replacements.items():
            text = text.replace("{{" + key + "}}", str(val))
        return text
