"""Configuration loaded from environment variables + YAML prompt templates."""

from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).parent


class Settings:
    # Telnyx (SMS carrier)
    telnyx_api_key: str = os.getenv("TELNYX_API_KEY", "")
    telnyx_from: str = os.getenv("TELNYX_FROM", "")
    telnyx_messaging_profile_id: str = os.getenv("TELNYX_MESSAGING_PROFILE_ID", "")
    telnyx_public_key: str = os.getenv("TELNYX_WEBHOOK_PUBLIC_KEY", "")
    # Business owner gets booking/emergency/hot-lead alerts
    business_owner_phone: str = os.getenv("BUSINESS_OWNER_PHONE", "")
    review_link: str = os.getenv("REVIEW_LINK", "")

    # Review Engine (#17) — T+2h thank-you + review request, then follow-ups
    review_follow_up_hours: int = int(os.getenv("REVIEW_FOLLOW_UP_HOURS", "48"))
    review_email_delay_hours: int = int(os.getenv("REVIEW_EMAIL_DELAY_HOURS", "72"))
    # Quiet-lead no-reply follow-ups (2026-08-18): two touches with DIFFERENT
    # angles + opt-out, instead of one generic nudge. Follows HighLevel's
    # multi-touch pattern.
    follow_up_hours: int = int(os.getenv("FOLLOW_UP_HOURS", "24"))    # 1st nudge
    follow_up_2_hours: int = int(os.getenv("FOLLOW_UP_2_HOURS", "48"))  # 2nd, different angle
    # Email fallback (customer never engages with texts). SMTP creds empty = email skipped.
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_pass: str = os.getenv("SMTP_PASS", "")
    smtp_from: str = os.getenv("SMTP_FROM", "")

    # LLM
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    # Fallback chain: same-base fallback model, then local Ollama (degraded but always up)
    llm_fallback_model: str = os.getenv("LLM_FALLBACK_MODEL", "")
    llm_fallback2_base_url: str = os.getenv("LLM_FALLBACK2_BASE_URL", "http://127.0.0.1:11434/v1")
    llm_fallback2_model: str = os.getenv("LLM_FALLBACK2_MODEL", "")
    llm_fallback2_api_key: str = os.getenv("LLM_FALLBACK2_API_KEY", "")

    # Business
    business_type: str = os.getenv("BUSINESS_TYPE", "default")
    business_name: str = os.getenv("BUSINESS_NAME", "Your Business")
    service_area: str = os.getenv("SERVICE_AREA", "Your Area")
    price_percent: int = int(os.getenv("PRICE_PERCENT", "15"))  # % of booked revenue — 15% default (2026-07-31)

    # Server
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8080"))

    # Paths
    prompt_dir: Path = PROJECT_ROOT / "prompts"
    db_path: Path = Path(os.getenv("DB_PATH", str(PROJECT_ROOT / "data" / "conversations.db")))

    @property
    def telnyx_configured(self) -> bool:
        """True once the Telnyx number is purchased and set in TELNYX_FROM."""
        return bool(self.telnyx_api_key and self.telnyx_from)

    @property
    def outbound_from(self) -> str:
        """Sender for outbound SMS — the Telnyx toll-free number."""
        return self.telnyx_from

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_pass and self.smtp_from)


@lru_cache()
def get_settings() -> Settings:
    return Settings()
