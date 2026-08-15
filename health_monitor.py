"""missed-call-ai health monitor — checks server, tunnel, and Telnyx balance.
Run standalone or via cron. Exits 0 when all healthy, 1 when anything is down.
"""

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
TUNNEL_FILE = PROJECT_ROOT / ".tunnel_url"

def load_api_key() -> str:
    env = PROJECT_ROOT / ".env"
    if not env.exists():
        print("ERROR: .env not found")
        sys.exit(1)
    with open(env) as f:
        for line in f:
            if line.startswith("TELNYX_API_KEY="):
                return line.strip().split("=", 1)[1]
    print("ERROR: TELNYX_API_KEY not set")
    sys.exit(1)


def check_server() -> bool:
    try:
        r = urllib.request.urlopen("http://localhost:8080/health", timeout=10)
        data = json.loads(r.read())
        ok = data.get("status") == "ok"
        if ok:
            print(f"✓ Server: {data['business_type']} (model={data['model']})")
        return ok
    except Exception as e:
        print(f"✗ Server DOWN: {e}")
        return False


def check_tunnel() -> bool:
    if not TUNNEL_FILE.exists():
        print("✗ Tunnel: .tunnel_url file missing")
        return False
    url = TUNNEL_FILE.read_text().strip()
    try:
        r = urllib.request.urlopen(f"{url}/health", timeout=10)
        ok = r.status == 200
        if ok:
            print(f"✓ Tunnel: {url}")
        return ok
    except Exception as e:
        print(f"✗ Tunnel DOWN ({url}): {e}")
        return False


def check_balance() -> bool:
    api_key = load_api_key()
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
                print(f"⚠ LOW BALANCE: ${balance:,.2f} — refill soon!")
            return True
    except urllib.error.HTTPError as e:
        print(f"✗ Balance check HTTP {e.code}")
        return False
    except Exception as e:
        print(f"✗ Balance check failed: {e}")
        return False


def main():
    ok = True
    ok &= check_server()
    ok &= check_tunnel()
    ok &= check_balance()
    if ok:
        # Silent on success — cron delivers only on failure
        sys.exit(0)
    else:
        print("\nSOME CHECKS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()