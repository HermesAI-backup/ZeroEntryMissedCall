"""Stuck-AI review queue — conversations that need a human look.

HighLevel's gap (per independent reviews) is silent bot failures; ours is the
same class of problem — a conversation can end without a booking and nothing
surfaces it. This scans the DB for conversations where the AI effectively
died and prints them for the daily report / review:

  unanswered   — state='active' AND the LAST message is from the customer
                 (the AI never replied — silent death: an LLM error was
                 caught and logged, or the handler bailed). Customer texted
                 and got silence.
  maxed_unbooked — response_count >= max_responses AND branch != 'booked'
                 (the conversation hit the 10-message cap without booking —
                 a lost lead the owner should eyeball).
  stale_quiet  — state='active' with ZERO customer messages and created > 2
                 days ago (missed-call text went out, both follow-up nudges
                 fired, customer never engaged — informational, could be a
                 dead/wrong number).

Silent on clean (cron-friendly: the daily no_agent wrapper delivers stdout
verbatim, so a clean day must produce zero bytes). --alert sends the queue
to Telegram via HermesNives_bot (same creds path as health_monitor) with
state dedupe so a persistent stuck conversation alerts once, not every day.

Usage:
    python review_queue.py                 # print queue (silent when clean)
    python review_queue.py --days 3        # window
    python review_queue.py --alert         # Telegram on new/changed queue
"""
from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import sys
import urllib.request
import urllib.error
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
STATE_FILE = PROJECT / ".review_queue_state.json"  # gitignored (add to .gitignore)


def load_env_key(key: str) -> str:
    env = PROJECT / ".env"
    if not env.exists():
        return ""
    with open(env, encoding="utf-8") as f:
        for line in f:
            if line.startswith(f"{key}="):
                return line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _utcnow() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _parse_dt(raw) -> datetime.datetime | None:
    """Parse a SQLAlchemy-stored datetime string (space separator, maybe µs)."""
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", ""))
    except ValueError:
        return None


def _has_user_messages(conn: sqlite3.Connection, conversation_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM messages WHERE conversation_id=? AND role='user' LIMIT 1",
        (conversation_id,),
    ).fetchone()
    return row is not None


def build_queue(conn: sqlite3.Connection, since: datetime.datetime) -> list[dict]:
    conn.row_factory = sqlite3.Row
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """SELECT c.id, c.client_id, c.business_type, c.state, c.branch,
                  c.response_count, c.max_responses, c.created_at, c.updated_at
           FROM conversations c WHERE c.created_at >= ?""",
        (since_str,),
    ).fetchall()

    client_names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM clients")}

    def client_key(row) -> str:
        cid = row["client_id"]
        if cid and cid in client_names:
            return client_names[cid]
        return row["business_type"] or "unknown"

    queue: list[dict] = []
    for row in rows:
        # last message role
        last = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id=? "
            "ORDER BY id DESC LIMIT 1", (row["id"],),
        ).fetchone()
        last_role = last["role"] if last else None
        last_text = (last["content"] if last else "")[:80]
        created_dt = _parse_dt(row["created_at"])

        reason = None
        if row["state"] == "active" and last_role == "user":
            reason = "unanswered"
        elif (row["response_count"] or 0) >= (row["max_responses"] or 0) \
                and row["branch"] != "booked":
            reason = "maxed_unbooked"
        elif row["state"] == "active" and created_dt \
                and (_utcnow() - created_dt).days >= 2 \
                and _has_user_messages(conn, row["id"]) is False:
            reason = "stale_quiet"
        if not reason:
            continue
        queue.append({
            "conv_id": row["id"],
            "client": client_key(row),
            "state": row["state"],
            "branch": row["branch"] or "-",
            "reason": reason,
            "last_role": last_role or "-",
            "last_text": last_text,
            "created": (row["created_at"] or "")[:16],
        })
    return queue


def render(queue: list[dict]) -> str:
    if not queue:
        return ""
    lines = ["⚠️ AI REVIEW QUEUE — conversations needing a look:"]
    lines.append(f"{'conv':>5} {'client':<18} {'reason':<14} {'branch':<10} {'created':<17} last")
    lines.append("-" * 90)
    for q in queue:
        lines.append(
            f"{q['conv_id']:>5} {q['client'][:18]:<18} {q['reason']:<14} "
            f"{q['branch'][:10]:<10} {q['created']:<17} {q['last_text'][:40]}"
        )
    return "\n".join(lines)


# --- Telegram alert (HermesNives_bot, same creds fallback as health_monitor) --

def _telegram_creds() -> tuple[str, str]:
    token = load_env_key("TELEGRAM_BOT_TOKEN")
    chat = load_env_key("TELEGRAM_CHAT_ID")
    if not token or not chat:
        alt = Path.home() / "AppData/Local/hermes/profiles/gameforge/.env"
        if alt.exists():
            for line in alt.read_text(encoding="utf-8").splitlines():
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    token = line.strip().split("=", 1)[1].strip().strip('"')
                if line.startswith("TELEGRAM_HOME_CHANNEL="):
                    chat = line.strip().split("=", 1)[1].strip().strip('"')
    return token, chat


def _send_telegram(message: str) -> bool:
    token, chat = _telegram_creds()
    if not token or not chat:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({"chat_id": chat, "text": message}).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _load_state() -> str:
    if STATE_FILE.exists():
        try:
            return STATE_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--alert", action="store_true",
                        help="Telegram the queue when it CHANGES (dedupe via state file)")
    args = parser.parse_args()

    db_path = load_env_key("DB_PATH") or str(PROJECT / "data" / "conversations.db")
    if not Path(db_path).exists():
        return 0
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    since = _utcnow() - datetime.timedelta(days=args.days)
    queue = build_queue(conn, since)
    conn.close()

    text = render(queue)
    if args.alert:
        sig = json.dumps(queue, sort_keys=True) if queue else ""
        if sig and sig != _load_state():
            if _send_telegram(text):
                STATE_FILE.write_text(sig, encoding="utf-8")
        elif not queue and _load_state():
            # queue cleared — reset dedupe so a NEW stuck conv alerts
            STATE_FILE.write_text("", encoding="utf-8")
    else:
        if text:
            print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
