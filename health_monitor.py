"""missed-call-ai health monitor — checks server, tunnel, and Telnyx balance.
Run standalone or via cron. Exits 0 when all healthy, 1 when anything is down.

Usage:
    python health_monitor.py           # print results, exit appropriately
    python health_monitor.py --alert   # notify Sevin on failure (Telegram first,
                                       # SMS fallback only if Telegram is down)

2026-08-18: alerts switched from SMS-only to Telegram-first (Sevin: "waste money
on sms"). Added state-transition dedupe: a sustained outage now alerts ONCE on
the transition into failure + once on recovery, instead of every 5-minute cron
tick (he got 6 SMS in one night from the same localtunnel outage).
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
TUNNEL_FILE = PROJECT_ROOT / ".tunnel_url"
STATE_FILE = PROJECT_ROOT / ".health_state.json"
ALERT_COOLDOWN = 3600  # seconds — minimum gap between repeat alerts for same check
TELEGRAM_API = "https://api.telegram.org"


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


def _telegram_creds() -> tuple[str, str]:
    """Bot token + chat id. Prefer project .env, fall back to the gameforge
    profile's working Telegram bot (HermesNives_bot -> Sevin)."""
    token = load_env("TELEGRAM_BOT_TOKEN")
    chat = load_env("TELEGRAM_CHAT_ID")
    if token and chat:
        return token, chat
    # Fallback: gameforge profile has a verified-working bot
    alt = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "profiles" / "gameforge" / ".env"
    if alt.exists():
        vals = {}
        for line in alt.read_text(encoding="utf-8").splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                vals["token"] = line.split("=", 1)[1].strip()
            elif line.startswith("TELEGRAM_HOME_CHANNEL="):
                vals["chat"] = line.split("=", 1)[1].strip()
        if vals.get("token") and vals.get("chat"):
            return vals["token"], vals["chat"]
    return "", ""


def _send_telegram(body: str) -> bool:
    token, chat = _telegram_creds()
    if not token or not chat:
        print("  (no telegram creds available)")
        return False
    payload = json.dumps({"chat_id": chat, "text": f"🚨 missed-call-ai ALERT:\n{body}"}).encode()
    req = urllib.request.Request(
        f"{TELEGRAM_API}/bot{token}/sendMessage",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
            ok = data.get("ok", False)
            if ok:
                print("  ✓ Telegram alert sent")
            return ok
    except Exception as e:
        print(f"  ✗ Telegram send failed: {e}")
        return False


def _send_alert_sms(body: str):
    """Last-resort SMS fallback (only when Telegram is unreachable)."""
    api_key = load_env("TELNYX_API_KEY")
    from_number = load_env("TELNYX_FROM")
    owner_phone = load_env("BUSINESS_OWNER_PHONE")
    if not api_key or not owner_phone:
        print("  ⚠ Cannot send SMS fallback — missing TELNYX_API_KEY or BUSINESS_OWNER_PHONE")
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
            print(f"  ✓ Alert SMS sent to {owner_phone} (id={msg_id})")
    except Exception as e:
        print(f"  ✗ Alert SMS FAILED: {e}")


def notify(msg: str):
    """Telegram-first, SMS fallback. Called only on state transitions."""
    if _send_telegram(msg):
        return
    _send_alert_sms(msg)


# ---------------------------------------------------------------------------
# State-transition dedupe: a check that stays down alerts once (on the way
# down) and once on recovery — never once per cron tick.
# ---------------------------------------------------------------------------
def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


def _should_alert(check: str, ok: bool, state: dict) -> bool:
    """True if this check's ok/fail is a NEW transition (or cooldown elapsed)."""
    entry = state.get(check, {})
    prev_ok = entry.get("ok")
    last_alert = entry.get("last_alert", 0)
    now = time.time()

    if prev_ok is None:
        # First run ever: remember state but don't alert (baseline).
        entry["ok"] = ok
        entry["last_alert"] = now
        state[check] = entry
        return False
    if prev_ok == ok:
        # Same state as before — no transition. Re-alert only if it's been
        # down a long while (cooldown) so a multi-hour outage nudges once/hour.
        if not ok and now - last_alert >= ALERT_COOLDOWN:
            entry["last_alert"] = now
            state[check] = entry
            return True
        return False
    # Transition ok<->fail
    entry["ok"] = ok
    entry["last_alert"] = now
    state[check] = entry
    return True


def check_server(alert: bool = False, state: dict | None = None) -> bool:
    try:
        req = urllib.request.Request(
            "http://localhost:8080/health",
            headers={"User-Agent": "health-monitor/1.0"},
        )
        r = urllib.request.urlopen(req, timeout=10)
        data = json.loads(r.read())
        ok = data.get("status") == "ok"
        if ok and not alert:
            print(f"✓ Server: {data['business_type']} (model={data['model']})")
        return ok
    except Exception as e:
        msg = f"Server DOWN: {e}"
        print(f"✗ {msg}")
        if alert and state is not None and _should_alert("server", False, state):
            notify(msg)
        return False


def check_tunnel(alert: bool = False, state: dict | None = None) -> bool:
    if not TUNNEL_FILE.exists():
        msg = "Tunnel: .tunnel_url file missing"
        print(f"✗ {msg}")
        if alert and state is not None and _should_alert("tunnel", False, state):
            notify(msg)
        return False
    url = TUNNEL_FILE.read_text().strip()
    try:
        # Browser-ish UA: some tunnel providers 403 the default Python-urllib UA
        req = urllib.request.Request(
            f"{url}/health",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) health-monitor"},
        )
        r = urllib.request.urlopen(req, timeout=12)
        ok = r.status == 200
        if ok and not alert:
            print(f"✓ Tunnel: {url}")
        return ok
    except Exception as e:
        msg = f"Tunnel DOWN ({url}): {e}"
        print(f"✗ {msg}")
        if alert and state is not None and _should_alert("tunnel", False, state):
            notify(msg)
        return False


def check_balance(alert: bool = False, state: dict | None = None) -> bool:
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
            "User-Agent": "health-monitor/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
            balance_raw = data.get("data", {}).get("balance", "0")
            balance = float(balance_raw) if isinstance(balance_raw, str) else balance_raw
            currency = data.get("data", {}).get("currency", "USD")
            if not alert:
                print(f"✓ Balance: {currency} ${balance:,.2f}")
            if balance < 5:
                msg = f"LOW BALANCE: ${balance:,.2f} — refill now!"
                print(f"⚠ {msg}")
                if alert and state is not None and _should_alert("balance", False, state):
                    notify(msg)
            return True
    except urllib.error.HTTPError as e:
        msg = f"Balance check HTTP {e.code}"
        print(f"✗ {msg}")
        if alert and state is not None and _should_alert("balance", False, state):
            notify(msg)
        return False
    except Exception as e:
        msg = f"Balance check failed: {e}"
        print(f"✗ {msg}")
        if alert and state is not None and _should_alert("balance", False, state):
            notify(msg)
        return False


def check_llm(alert: bool = False, state: dict | None = None) -> bool:
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
        if ok and not alert:
            print("✓ LLM proxy: 127.0.0.1:8645")
        return ok
    except Exception as e:
        msg = f"LLM proxy DOWN: {e}"
        print(f"✗ {msg}")
        if alert and state is not None and _should_alert("llm", False, state):
            notify(msg)
        return False


def main():
    alert = "--alert" in sys.argv
    state = _load_state()

    ok = True
    ok &= check_server(alert, state)
    ok &= check_llm(alert, state)
    ok &= check_tunnel(alert, state)
    ok &= check_balance(alert, state)
    _save_state(state)

    if ok:
        # Silent on success — the cron (no_agent mode) delivers stdout verbatim
        # every tick, so healthy runs must print NOTHING. Failures print + exit 1.
        sys.exit(0)
    else:
        print("\nSOME CHECKS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
