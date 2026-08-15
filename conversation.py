"""LLM-powered conversation engine — generates replies, manages state."""

from __future__ import annotations

import yaml
from pathlib import Path

from config import get_settings
from llm_client import LLMClient
from branching import evaluate_branch, BranchName

settings = get_settings()


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

        reply = await self.llm.chat(messages=messages, temperature=0.7, max_tokens=300)

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

        system_prompt = (
            "You are a booking-detail extractor. Read the conversation below and "
            "extract the customer's booking information. Return ONLY valid JSON with "
            "the exact keys specified. Use an empty string for any field the customer "
            "has NOT yet provided."
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

        # Defensive: ensure all keys exist, fill missing with ""
        return {
            "customer_name": result.get("customer_name", ""),
            "address": result.get("address", ""),
            "appt_date": result.get("appt_date", ""),
            "appt_time": result.get("appt_time", ""),
        }

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
