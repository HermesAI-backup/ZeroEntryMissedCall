"""Live integration test: localtunnel_watchdog dual-webhook sync.

Verifies the watchdog's TeXML + messaging-profile webhook sync against the
live Telnyx API. Idempotent — re-PATCHes the same URL, no tunnel rotation.

Usage: python test_watchdog_webhooks.py
Exit 0 = all green, 1 = any check failed. (Plain asserts, like
test_review_engine.py — no pytest dependency.)
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from localtunnel_watchdog import (  # noqa: E402
    MESSAGING_PROFILE_ID,
    TEXML_APP_ID,
    update_messaging_profile_webhook,
    update_texml_webhook,
)

PROJECT = Path(__file__).parent


def _api_key() -> str:
    for line in (PROJECT / ".env").read_text().splitlines():
        if line.startswith("TELNYX_API_KEY="):
            return line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("TELNYX_API_KEY not found in .env")


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_api_key()}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())["data"]


def main() -> int:
    tunnel = (PROJECT / ".tunnel_url").read_text().strip()

    print(f"tunnel: {tunnel}")
    assert update_texml_webhook(tunnel), "TeXML sync returned False"
    assert update_messaging_profile_webhook(tunnel), "messaging profile sync returned False"

    texml = _get(f"https://api.telnyx.com/v2/texml_applications/{TEXML_APP_ID}")
    profile = _get(f"https://api.telnyx.com/v2/messaging_profiles/{MESSAGING_PROFILE_ID}")

    print(f"TeXML voice_url:       {texml['voice_url']}")
    print(f"Messaging webhook_url: {profile['webhook_url']}")
    assert texml["voice_url"] == f"{tunnel}/webhooks/telnyx/call"
    assert profile["webhook_url"] == f"{tunnel}/webhooks/telnyx"

    print("PASS: dual webhook sync verified against live Telnyx API")
    return 0


if __name__ == "__main__":
    sys.exit(main())
