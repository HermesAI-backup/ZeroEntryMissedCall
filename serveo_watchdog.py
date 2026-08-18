"""Serveo tunnel watchdog — auto-restarts the tunnel and updates BOTH Telnyx
webhooks on reconnect (TeXML voice_url = missed-call trigger + messaging
profile webhook_url = inbound SMS replies).

2026-08-18: switched from localtunnel (free tier was unreachable from this IP
after aggressive restarts; the old watchdog also only synced the messaging
webhook, so the TeXML voice_url went stale and missed calls stopped firing
text-backs). Serveo is SSH-based and reachable here. Health checks use a
browser-ish UA — some tunnel providers 403 the default Python-urllib UA.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

from config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("serveo-watchdog")

settings = get_settings()

SERVEO_HOST = "serveo.net"
LOCAL_PORT = 8080
POLL_INTERVAL = 15  # seconds between tunnel health checks
MAX_RESTART_BACKOFF = 300  # max seconds between restart attempts

# Browser-ish UA for health checks (tunnel providers 403 bare Python-urllib)
HEALTH_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) serveo-watchdog"


def _telnyx_api_key() -> str:
    """settings may have an empty key; fall back to the .env file."""
    if getattr(settings, "telnyx_api_key", ""):
        return settings.telnyx_api_key
    env = Path(__file__).resolve().parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("TELNYX_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def update_texml_voice_url(base_url: str) -> bool:
    """Point the TeXML application's voice_url at the tunnel (missed-call trigger)."""
    api_key = _telnyx_api_key()
    texml_id = getattr(settings, "telnyx_texml_app_id", "") or "3026763910813320521"
    if not api_key:
        logger.warning("TELNYX_API_KEY not set — skipping TeXML voice_url update")
        return False

    webhook_url = f"{base_url}/webhooks/telnyx/call"
    payload = json.dumps({"voice_url": webhook_url}).encode()
    req = urllib.request.Request(
        f"https://api.telnyx.com/v2/texml_applications/{texml_id}",
        data=payload,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
            url_on_file = data.get("data", {}).get("voice_url", "")
            if url_on_file == webhook_url:
                logger.info("TeXML voice_url updated → %s", webhook_url)
                return True
            logger.warning(
                "TeXML update returned %s (expected %s)", url_on_file, webhook_url
            )
            return False
    except urllib.error.HTTPError as e:
        logger.error("TeXML webhook update HTTP %s: %s", e.code, e.read().decode()[:300])
        return False
    except Exception as e:  # noqa: BLE001
        logger.error("TeXML webhook update failed: %s", e)
        return False


def update_messaging_profile_webhook(base_url: str) -> bool:
    """Point the Telnyx messaging profile's webhook at the current tunnel URL.

    Without this, inbound SMS replies die whenever the tunnel URL changes —
    outbound keeps working (API direct) but nobody can text back.
    """
    profile_id = getattr(settings, "telnyx_messaging_profile_id", "")
    api_key = _telnyx_api_key()
    if not profile_id or not api_key:
        logger.warning(
            "TELNYX_MESSAGING_PROFILE_ID / TELNYX_API_KEY not set — skipping messaging profile update"
        )
        return False

    webhook_url = f"{base_url}/webhooks/telnyx"
    payload = json.dumps({"webhook_url": webhook_url}).encode()
    req = urllib.request.Request(
        f"https://api.telnyx.com/v2/messaging_profiles/{profile_id}",
        data=payload,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
            url_on_file = data.get("data", {}).get("webhook_url", "")
            if url_on_file == webhook_url:
                logger.info("Messaging profile webhook_url updated → %s", webhook_url)
                return True
            logger.warning(
                "Messaging profile update returned %s (expected %s)",
                url_on_file,
                webhook_url,
            )
            return False
    except urllib.error.HTTPError as e:
        logger.error("Messaging profile webhook update HTTP %s: %s", e.code, e.read().decode()[:300])
        return False
    except Exception as e:  # noqa: BLE001
        logger.error("Messaging profile webhook update failed: %s", e)
        return False


def start_tunnel() -> tuple[subprocess.Popen | None, str | None]:
    """Start the SSH tunnel and return (process, url). Blocks until URL is found."""
    logger.info("Starting serveo tunnel...")
    proc = subprocess.Popen(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "ConnectTimeout=20",
            "-R", f"80:localhost:{LOCAL_PORT}",
            SERVEO_HOST,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    url = None
    timeout = 45
    start = time.time()
    while time.time() - start < timeout:
        line = proc.stdout.readline() if proc.stdout else ""
        if not line:
            if proc.poll() is not None:
                logger.error("Serveo exited early (code %s)", proc.returncode)
                break
            time.sleep(0.5)
            continue
        logger.debug("Serveo: %s", line.strip())
        # Look for: Forwarding HTTP traffic from https://xxxx.serveousercontent.com
        match = re.search(r"https://([a-zA-Z0-9-]+\.serveousercontent\.com)", line)
        if match:
            url = match.group(1)
            logger.info("Tunnel URL: https://%s", url)
            break

    return proc, url


def check_tunnel(url: str) -> bool:
    """True if the tunnel is reachable and proxying to our server."""
    try:
        req = urllib.request.Request(
            f"https://{url}/health",
            headers={"User-Agent": HEALTH_UA},
        )
        r = urllib.request.urlopen(req, timeout=12)
        return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def main():
    logger.info("=" * 50)
    logger.info("Serveo Watchdog Started")
    logger.info("=" * 50)

    backoff = 1

    while True:
        try:
            proc, url = start_tunnel()

            if not url:
                logger.warning("Failed to get tunnel URL. Retry in %ss...", backoff)
                if proc:
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_RESTART_BACKOFF)
                continue

            # Reset backoff on success
            backoff = 1

            base_url = f"https://{url}"

            # Save tunnel URL so other scripts can reference it
            url_file = Path(__file__).resolve().parent / ".tunnel_url"
            url_file.write_text(base_url)
            logger.info("Saved tunnel URL to .tunnel_url")

            # Sync BOTH Telnyx webhooks: TeXML voice_url (missed-call trigger)
            # + messaging profile (inbound SMS replies)
            if update_texml_voice_url(base_url):
                logger.info("TeXML voice_url synced")
            else:
                logger.warning("TeXML voice_url sync FAILED — will retry next cycle")

            if update_messaging_profile_webhook(base_url):
                logger.info("Messaging profile webhook synced")
            else:
                logger.warning("Messaging profile webhook sync FAILED — will retry next cycle")

            logger.info(
                "Tunnel running (PID %d). Monitoring every %ds...",
                proc.pid,
                POLL_INTERVAL,
            )

            # Monitor the tunnel
            while True:
                time.sleep(POLL_INTERVAL)
                ret = proc.poll()
                if ret is not None:
                    logger.warning("Tunnel exited (code %s). Restarting...", ret)
                    break

                if not check_tunnel(url):
                    logger.warning("Health check failed. Restarting...")
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
                    break

        except KeyboardInterrupt:
            logger.info("Shutting down...")
            sys.exit(0)
        except Exception as e:  # noqa: BLE001
            logger.error("Unexpected error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
