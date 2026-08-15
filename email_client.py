"""Email client — fallback channel for the Review Engine (#17).

Sends the review request via SMTP when a customer never engages with
texts. Config comes from SMTP_* env vars; if not configured the client
logs "would send" and returns False (same pattern as telnyx_client).
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from config import get_settings

logger = logging.getLogger("missed-call-ai")

settings = get_settings()


def send_email(to: str, subject: str, body: str) -> bool:
    """Send a plain-text email. Returns True on success.

    If SMTP is not configured, logs the would-be email and returns False
    so the caller can mark the task failed/skipped without crashing.
    """
    if not settings.smtp_configured:
        logger.warning(
            "SMTP not configured — would email %s | subject: %s", to, subject
        )
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg.set_content(body)

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_pass)
            server.send_message(msg)
        logger.info("Emailed %s | subject: %s", to, subject)
        return True
    except Exception as e:
        logger.error("Failed to email %s: %s", to, e)
        return False
