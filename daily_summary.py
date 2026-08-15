"""Daily status summary for missed-call-ai — always reports. For cron."""

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent


def load_env_key(key: str) -> str:
    env = PROJECT_ROOT / ".env"
    if not env.exists():
        return ""
    with open(env) as f:
        for line in f:
            if line.startswith(f"{key}="):
                return line.strip().split("=", 1)[1]
    return ""


def main():
    api_key = load_env_key("TELNYX_API_KEY")

    # Server
    try:
        r = urllib.request.urlopen("http://localhost:8080/health", timeout=10)
        h = json.loads(r.read())
        print(f"Server:  UP — {h['business_type']} ({h['model']})")
    except Exception as e:
        print(f"Server:  DOWN — {e}")

    # Tunnel
    tunnel_file = PROJECT_ROOT / ".tunnel_url"
    if tunnel_file.exists():
        url = tunnel_file.read_text().strip()
        try:
            r = urllib.request.urlopen(f"{url}/health", timeout=10)
            print(f"Tunnel:  UP — {url}")
        except Exception as e:
            print(f"Tunnel:  DOWN — {e}")
    else:
        print("Tunnel:  no .tunnel_url file")

    # Balance
    if api_key:
        req = urllib.request.Request(
            "https://api.telnyx.com/v2/balance",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode())
                raw = data.get("data", {}).get("balance", "0")
                bal = float(raw) if isinstance(raw, str) else raw
                print(f"Balance: ${bal:,.2f}")
        except Exception as e:
            print(f"Balance: error — {e}")
    else:
        print("Balance: no API key")


if __name__ == "__main__":
    main()