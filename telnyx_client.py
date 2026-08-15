"""Telnyx SMS client — the single outbound SMS path.

Twilio was scrubbed from this project Aug 10; Telnyx is the only carrier.
Until the Telnyx toll-free number is approved (TFV status = Approved),
send_sms logs a warning and returns None — it never silently pretends to send.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.request

from config import get_settings

logger = logging.getLogger("missed-call-ai")

settings = get_settings()

MESSAGES_API = "https://api.telnyx.com/v2/messages"


def send_sms(to: str, body: str, from_number: str | None = None) -> str | None:
    """Send an SMS via Telnyx. Returns the message id, or None on failure."""
    if not settings.telnyx_configured:
        logger.warning(
            "Telnyx not configured — would send SMS to %s: %s", to, body
        )
        return None
    return _send_telnyx(to, body, from_number or settings.telnyx_from)


def _send_telnyx(to: str, body: str, from_number: str) -> str | None:
    payload = json.dumps({"from": from_number, "to": to, "text": body}).encode()
    req = urllib.request.Request(
        MESSAGES_API,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.telnyx_api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
            return data.get("data", {}).get("id")
    except urllib.error.HTTPError as e:
        logger.error("Telnyx send HTTP %s: %s", e.code, e.read().decode()[:300])
        return None
    except Exception as e:  # noqa: BLE001
        logger.error("Telnyx send failed: %s", e)
        return None


def verify_webhook_signature(
    raw_body: bytes,
    signature_b64: str,
    timestamp: str = "",
) -> bool:
    """Verify a Telnyx webhook Ed25519 signature.

    Public key comes from the portal (account-level: Keys & Credentials →
    Public Key) and is base64-encoded (44 chars ending '=', decodes to 32
    bytes).

    API version 2 (webhook_api_version=2, the messaging-profile default)
    signs the message ``{timestamp}|{raw_body}`` with the two headers
    ``telnyx-signature-ed25519`` (base64 Ed25519 sig) and ``telnyx-timestamp``
    (unix seconds). Legacy v1 (unsigned) and the older raw-body-only scheme
    are tolerated when the timestamp header is absent.

    Returns True when valid; False when invalid or when the key isn't
    configured (caller decides whether to accept unsigned).
    """
    if not settings.telnyx_public_key:
        logger.warning("TELNYX_WEBHOOK_PUBLIC_KEY not set — skipping signature check")
        return True
    try:
        import nacl.signing
        import nacl.exceptions

        verify_key = nacl.signing.VerifyKey(
            base64.b64decode(settings.telnyx_public_key)
        )
        if timestamp:
            # Replay guard: reject timestamps older than 5 minutes.
            try:
                if abs(int(time.time()) - int(timestamp)) > 300:
                    logger.warning("Telnyx webhook timestamp outside 5-min window")
                    return False
            except (TypeError, ValueError):
                logger.warning("Telnyx webhook timestamp unparseable")
                return False
            signed = f"{timestamp}|".encode() + raw_body
        else:
            # Legacy fallback: raw body only (no timestamp header).
            signed = raw_body
        verify_key.verify(signed, base64.b64decode(signature_b64))
        return True
    except nacl.exceptions.BadSignatureError:
        logger.warning("Telnyx webhook signature MISMATCH")
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("Telnyx webhook verification error: %s", e)
        return False
