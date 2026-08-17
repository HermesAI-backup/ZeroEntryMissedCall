"""missed-call-ai health monitor — checks server, tunnel, and Telnyx balance.
Run standalone or via cron. Exits 0 when all healthy, 1 when anything is down.

Usage:
    python health_monitor.py           # print results, exit appropriately
    python health_monitor.py --alert   # also SMS Sevin on failure
"""

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
TUNNEL_FILE = PROJECT_ROOT / ".tunnel_url"

_ALERT_SENT = False  # only send one alert SMS per run


def load_env(key: str) -> str:
    env = PROJECT_ROOT / ".env"
    if not env.exists():
        return ""
    with open(env) as f:
        for line in f:
            if line.startswith(f"{key}="):
                val = line.strip().split("=", 1)[1]
                return val.strip().strip('"').strip("'")
    return ""


def _send_alert_sms(body: str):
    """Send an SMS alert to the business owner via Telnyx."""
    global _ALERT_SENT
    if _ALERT_SENT:
        return
    _ALERT_SENT = True

    api_key = load_env("TELNYX_API_KEY")
    from_number = load_env("TELNYX_FROM")
    owner_phone = load_env("BUSINESS_OWNER_PHONE")

    if not api_key or not owner_phone:
        print("⚠ Cannot send alert — missing TELNYX_API_KEY or BUSINESS_OWNER_PHONE")
        return

    msg = f"🚨 missed-call-ai ALERT:\n{body}"
    payload = json.dumps({
        "from": from_number,
        "to": owner_phone,
        "text": msg,
    }).encode()

    req = urllib.request.Request(
        "https://api.telnyx.com/v2/messages",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
            msg_id = data.get("data", {}).get("id", "?")
            print(f"✓ Alert SMS sent to {owner_phone} (id={msg_id})")
    except Exception as e:
        print(f"✗ Alert SMS FAILED: {e}")


def check_server(alert: bool = False) -> bool:
    try:
        r = urllib.request.urlopen("http://localhost:8080/health", timeout=10)
        data = json.loads(r.read())
        ok = data.get("status") == "ok"
        if ok:
            print(f"✓ Server: {data['business_type']} (model={data['model']})")
        return ok
    except Exception as e:
        msg = f"Server DOWN: {e}"
        print(f"✗ {msg}")
        if alert:
            _send_alert_sms(msg)
        return False


def check_tunnel(alert: bool = False) -> bool:
    if not TUNNEL_FILE.exists():
        msg = "Tunnel: .tunnel_url file missing"
        print(f"✗ {msg}")
        if alert:
            _send_alert_sms(msg)
        return False
    url = TUNNEL_FILE.read_text().strip()
    try:
        r = urllib.request.urlopen(f"{url}/health", timeout=10)
        ok = r.status == 200
        if ok:
            print(f"✓ Tunnel: {url}")
        return ok
    except Exception as e:
        msg = f"Tunnel DOWN ({url}): {e}"
        print(f"✗ {msg}")
        if alert:
            _send_alert_sms(msg)
        return False


def check_balance(alert: bool = False) -> bool:
    api_key = load_env("TELNYX_API_KEY")
    if not api_key:
        print("✗ Balance: no API key")
        return False
    req = urllib.request.Request(
        "https://api.telnyx.com/v2/balance",
        method="GET",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
            balance_raw = data.get("data", {}).get("balance", "0")
            balance = float(balance_raw) if isinstance(balance_raw, str) else balance_raw
            currency = data.get("data", {}).get("currency", "USD")
            print(f"✓ Balance: {currency} ${balance:,.2f}")
            if balance < 5:
                msg = f"LOW BALANCE: ${balance:,.2f} — refill now!"
                print(f"⚠ {msg}")
                if alert:
                    _send_alert_sms(msg)
            return True
    except urllib.error.HTTPError as e:
        msg = f"Balance check HTTP {e.code}"
        print(f"✗ {msg}")
        if alert:
            _send_alert_sms(msg)
        return False
    except Exception as e:
        msg = f"Balance check failed: {e}"
        print(f"✗ {msg}")
        if alert:
            _send_alert_sms(msg)
        return False


def check_llm(alert: bool = False) -> bool:
    """Check the Hermes LLM proxy (127.0.0.1:8645) is serving — without it,
    the AI cannot reply (only the Ollama degraded tier would answer).
    The proxy accepts any bearer token (it attaches the real credential)."""
    try:
        key = load_env("LLM_API_KEY") or "unused"
        req = urllib.request.Request(
            "http://127.0.0.1:8645/v1/models",
            headers={
                "Authorization": f"Bearer {key}",
                # Proxy 403s the default "Python-urllib/3.11" UA
                "User-Agent": "health-monitor/1.0",
            },
        )
        r = urllib.request.urlopen(req, timeout=10)
        ok = r.status == 200
        if ok:
            print("✓ LLM proxy: 127.0.0.1:8645")
        return ok
    except Exception as e:
        msg = f"LLM proxy DOWN: {e}"
        print(f"✗ {msg}")
        if alert:
            _send_alert_sms(msg)
        return False


def main():
    alert = "--alert" in sys.argv

    ok = True
    ok &= check_server(alert)
    ok &= check_llm(alert)
    ok &= check_tunnel(alert)
    ok &= check_balance(alert)
    if ok:
        # Silent on success — cron delivers only on failure
        sys.exit(0)
    else:
        print("\nSOME CHECKS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()