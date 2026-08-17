"""Localtunnel watchdog — auto-restarts the tunnel and updates the Telnyx
TeXML application's voice_url on reconnect.

Operates the same as serveo_watchdog.py but uses localtunnel (npx) instead of
SSH, which gets around port-22 blocks on some networks.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("localtunnel-watchdog")

# Lazy-load API key without triggering load_dotenv() (which poisons subprocess
# environment and breaks localtunnel on Windows).
def _load_api_key() -> str:
    env = Path(__file__).resolve().parent / ".env"
    if not env.exists():
        return ""
    with open(env) as f:
        for line in f:
            if line.startswith("TELNYX_API_KEY="):
                val = line.strip().split("=", 1)[1]
                return val.strip().strip('"').strip("'")
    return ""

LOCAL_PORT = 8080
TEXML_APP_ID = "3026763910813320521"  # Zero Entry Missed Call AI
MESSAGING_PROFILE_ID = "40019fe8-601c-4385-afba-a202b791ba88"  # inbound SMS webhook
POLL_INTERVAL = 15  # seconds between tunnel health checks
MAX_RESTART_BACKOFF = 300  # max seconds between restart attempts


def update_texml_webhook(base_url: str) -> bool:
    """Point the TeXML application's voice_url at the current tunnel URL."""
    api_key = _load_api_key()
    if not api_key:
        logger.warning("TELNYX_API_KEY not set — skipping webhook update")
        return False

    webhook_url = f"{base_url}/webhooks/telnyx/call"
    payload = json.dumps({"voice_url": webhook_url}).encode()
    req = urllib.request.Request(
        f"https://api.telnyx.com/v2/texml_applications/{TEXML_APP_ID}",
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
            else:
                logger.warning(
                    "TeXML update returned %s (expected %s)", url_on_file, webhook_url
                )
                return False
    except urllib.error.HTTPError as e:
        logger.error("TeXML webhook update HTTP %s: %s", e.code, e.read().decode()[:300])
        return False
    except Exception as e:
        logger.error("TeXML webhook update failed: %s", e)
        return False


def update_messaging_profile_webhook(base_url: str) -> bool:
    """Point the messaging profile's webhook_url (inbound SMS) at the tunnel.

    Without this, inbound SMS replies die whenever the tunnel URL changes —
    outbound keeps working (API direct) but nobody can text back. Same
    pattern as update_texml_webhook.
    """
    api_key = _load_api_key()
    if not api_key:
        logger.warning("TELNYX_API_KEY not set — skipping messaging profile update")
        return False

    webhook_url = f"{base_url}/webhooks/telnyx"
    payload = json.dumps({"webhook_url": webhook_url}).encode()
    req = urllib.request.Request(
        f"https://api.telnyx.com/v2/messaging_profiles/{MESSAGING_PROFILE_ID}",
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
            else:
                logger.warning(
                    "Messaging profile update returned %s (expected %s)",
                    url_on_file,
                    webhook_url,
                )
                return False
    except urllib.error.HTTPError as e:
        logger.error("Messaging profile webhook update HTTP %s: %s", e.code, e.read().decode()[:300])
        return False
    except Exception as e:
        logger.error("Messaging profile webhook update failed: %s", e)
        return False


def start_tunnel() -> tuple[subprocess.Popen | None, str | None]:
    """Start localtunnel and return (process, url). Blocks until URL is found.

    Launches via bash -c because Windows Node.js .CMD wrappers hang under
    subprocess.PIPE without a real console, but bash handles them fine.
    """
    # Prefer global lt, fall back to npx localtunnel
    lt = shutil.which("lt") or shutil.which("lt.cmd")
    if lt:
        cmd = f'lt --port {LOCAL_PORT} 2>&1'
    else:
        npx = shutil.which("npx") or shutil.which("npx.cmd")
        if npx:
            cmd = f'npx localtunnel --port {LOCAL_PORT} 2>&1'
        else:
            logger.error("Neither lt nor npx found — install: npm i -g localtunnel")
            return None, None

    logger.info("Starting localtunnel on port %s via bash...", LOCAL_PORT)
    proc = subprocess.Popen(
        ["bash", "-c", cmd],
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
                logger.error("localtunnel exited early (code %s)", proc.returncode)
                break
            time.sleep(0.5)
            continue
        logger.debug("lt: %s", line.strip())
        # Parse: "your url is: https://xxxxx.loca.lt"
        match = re.search(r"your url is:\s*(https://[^\s]+)", line)
        if match:
            url = match.group(1).rstrip("/")
            logger.info("Tunnel URL: %s", url)
            break

    return proc, url


def check_tunnel(url: str) -> bool:
    """True if the tunnel is reachable and proxying to our server."""
    try:
        r = urllib.request.urlopen(f"{url}/health", timeout=10)
        return r.status == 200
    except Exception:
        return False


def main():
    logger.info("=" * 50)
    logger.info("Localtunnel Watchdog Started")
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
                    except Exception:
                        pass
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_RESTART_BACKOFF)
                continue

            # Reset backoff on success
            backoff = 1

            # Save URL so other scripts can reference it
            url_file = Path(__file__).resolve().parent / ".tunnel_url"
            url_file.write_text(url)
            logger.info("Saved tunnel URL to .tunnel_url")

            # Update TeXML voice_url + messaging profile webhook (inbound SMS)
            if update_texml_webhook(url):
                logger.info("TeXML webhook synced")
            else:
                logger.warning("TeXML webhook sync FAILED — will retry next cycle")

            if update_messaging_profile_webhook(url):
                logger.info("Messaging profile webhook synced")
            else:
                logger.warning("Messaging profile webhook sync FAILED — will retry next cycle")

            logger.info(
                "Tunnel running (PID %d). Monitoring every %ds...",
                proc.pid,
                POLL_INTERVAL,
            )

            # Monitor
            while True:
                time.sleep(POLL_INTERVAL)
                ret = proc.poll()
                if ret is not None:
                    logger.warning("Tunnel exited (code %s). Restarting...", ret)
                    break

                if not check_tunnel(url):
                    logger.warning("Tunnel health check failed. Restarting...")
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    break

        except KeyboardInterrupt:
            logger.info("Shutting down...")
            break
        except Exception as e:
            logger.error("Unexpected error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()