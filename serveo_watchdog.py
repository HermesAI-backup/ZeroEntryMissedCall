"""Serveo tunnel watchdog — auto-restarts the tunnel and updates the Telnyx
messaging-profile webhook URL on reconnect.

Twilio webhook updates were scrubbed Aug 10 — the only webhook that matters
now is the Telnyx messaging profile's webhook_url (/webhooks/telnyx).
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


def update_telnyx_webhook(base_url: str) -> bool:
    """Point the Telnyx messaging profile's webhook at the current tunnel URL."""
    profile_id = settings.telnyx_messaging_profile_id
    api_key = settings.telnyx_api_key
    if not profile_id or not api_key:
        logger.warning(
            "TELNYX_MESSAGING_PROFILE_ID / TELNYX_API_KEY not set — skipping webhook update"
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
            r.read()
    except urllib.error.HTTPError as e:
        logger.error("Telnyx webhook update HTTP %s: %s", e.code, e.read().decode()[:300])
        return False
    except Exception as e:  # noqa: BLE001
        logger.error("Telnyx webhook update failed: %s", e)
        return False

    # Save tunnel URL to a file for other scripts to reference
    url_file = Path(__file__).resolve().parent / ".tunnel_url"
    url_file.write_text(base_url)
    logger.info("Telnyx webhook updated -> %s", webhook_url)
    return True


def start_tunnel() -> tuple[subprocess.Popen, str]:
    """Start the SSH tunnel and return (process, url). Blocks until URL is found."""
    logger.info("Starting serveo tunnel...")
    proc = subprocess.Popen(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-R", f"80:localhost:{LOCAL_PORT}",
            SERVEO_HOST,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    url = None
    timeout = 30
    start = time.time()
    while time.time() - start < timeout:
        line = proc.stdout.readline() if proc.stdout else ""
        if not line:
            time.sleep(0.5)
            continue
        logger.debug("Serveo: %s", line.strip())
        # Look for: Forwarding HTTP traffic from https://xxxx.serveousercontent.com
        match = re.search(r"https://([a-zA-Z0-9-]+\.serveousercontent\.com)", line)
        if match:
            url = match.group(1)
            logger.info("Tunnel URL: https://%s", url)
            break
        # Check if process died
        if proc.poll() is not None:
            logger.error("Serveo exited early (code %s)", proc.returncode)
            break

    return proc, url


def main():
    logger.info("=" * 50)
    logger.info("Serveo Watchdog Started")
    logger.info("=" * 50)

    while True:
        try:
            proc, url = start_tunnel()

            if not url:
                logger.warning("Failed to get tunnel URL, restarting in 5s...")
                try:
                    proc.kill()
                except Exception:
                    pass
                time.sleep(5)
                continue

            base_url = f"https://{url}"

            # Point the Telnyx messaging profile at the new URL
            update_telnyx_webhook(base_url)

            logger.info("Tunnel running (PID %d). Monitoring every %ds...", proc.pid, POLL_INTERVAL)

            # Monitor the tunnel
            while True:
                time.sleep(POLL_INTERVAL)
                ret = proc.poll()
                if ret is not None:
                    logger.warning("Tunnel exited (code %s). Restarting...", ret)
                    break

                # Also check if the tunnel is actually reachable
                try:
                    r = urllib.request.urlopen(f"{base_url}/health", timeout=10)
                    if r.status == 200:
                        continue
                    logger.warning("Health check failed (status %s)", r.status)
                    break
                except Exception as e:
                    logger.warning("Health check failed: %s", e)
                    break

        except KeyboardInterrupt:
            logger.info("Shutting down...")
            sys.exit(0)
        except Exception as e:
            logger.error("Unexpected error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
