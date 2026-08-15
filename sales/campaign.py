"""Campaign tools — ringless voicemail drops and sales workflow."""

from __future__ import annotations

import logging
import datetime
from pathlib import Path
from typing import Literal

from config import get_settings

logger = logging.getLogger("sales.campaign")
settings = get_settings()

RECORDINGS_DIR = Path(__file__).resolve().parent.parent / "data" / "recordings"
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)


class VoicemailDrop:
    """Represents a ringless voicemail drop campaign.

    Ringless voicemail = upload a recording + phone list, and the service
    delivers the voicemail directly to the recipient's voicemail inbox
    without their phone ringing.

    Supported providers: Slack Broadcast (as shown in the video), plus
    alternative SlyBroadcast, CallLoop, etc.
    """

    def __init__(self, provider: str = "slack_broadcast"):
        self.provider = provider
        self.uploaded_list: list[str] = []
        self.recording_path: Path | None = None

    def add_numbers(self, numbers: list[str]):
        """Add phone numbers to the campaign."""
        self.uploaded_list.extend(numbers)
        logger.info("Added %d numbers to campaign", len(numbers))

    def set_recording(self, audio_path: str | Path):
        """Set the voicemail recording file."""
        self.recording_path = Path(audio_path)
        logger.info("Recording set: %s", self.recording_path)

    def preview(self) -> dict:
        """Preview campaign stats before sending."""
        return {
            "provider": self.provider,
            "numbers": len(self.uploaded_list),
            "recording": str(self.recording_path) if self.recording_path else None,
        }


# ---------------------------------------------------------------------------
# Dogfooding — the sales workflow (duplicate the main workflow as a sales tool)
# ---------------------------------------------------------------------------


def build_sales_prompt(
    service_name: str = "AI Missed Call Text Back",
    price: int | None = None,
) -> str:
    """Generate the 'dog fooding' sales prompt — sells the missed-call system
    to other business owners using the same AI text-back workflow.

    This replaces the service prompt (tree trimming / plumbing) with a
    sales prompt. The AI acts as YOU (the service provider) selling the
    missed-call system to the business owner who called back.

    Pricing: 15% of booked revenue (no flat fee, no cap, nothing up front).
    The `price` arg is legacy (flat $/month) — kept for call-site compat,
    ignored by the prompt.
    """
    price  # noqa: B018 — legacy flat-price arg, intentionally unused

    return f"""You are a friendly assistant representing {settings.business_name}. You're texting a business owner who just heard a voicemail about an AI-powered missed-call text-back system and called back.

ABOUT YOU:
You're a sales agent for {settings.business_name}. You help small business owners capture more leads by automatically texting back missed calls with AI that sounds like a real person.

OBJECTIVES:
1. Answer the business owner's questions about the service
2. Explain how it helps them close more sales (57% of sales go to whoever responds first)
3. Tell them it costs 15% of the jobs it books — no flat fee, nothing up front
4. If they ask detailed questions or seem interested, get them on a call
5. If they say it's too expensive, remind them a single missed job could be $200-500 — and they only pay 15% of what it books

RULES:
- Be warm and casual — talk like a real person
- Don't be pushy or salesy — be helpful
- If they're not interested, thank them and ask if they know anyone who might be
- Keep responses short — 2-3 sentences max
- Never ask more than one question at a time

- PRICING: 15% of booked revenue — no cap, no floor, nothing up front. If it doesn't book jobs, they don't pay. Small businesses pay less, big ones pay more.
"""
