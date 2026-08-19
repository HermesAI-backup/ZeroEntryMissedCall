"""Conversation audit export per client — for billing + quality review.

Dumps conversations (with full message transcripts + booking metadata) from
the SQLite DB to a readable file. Two formats:

  markdown (default) — one section per conversation: phone, state, branch,
                       booking details (customer/address/date/time/service),
                       then the full AI/customer transcript. For Sevin to
                       eyeball quality, or to attach to a billing dispute.
  csv                — one row per conversation (no transcripts): the
                       billing-relevant fields + booking metadata. For the
                       revenue-attribution reconciliation (Service Agreement
                       §4: customer number in conversation + booked within
                       30 days → attributed job).

Grouping: real clients by name; legacy convs without client_id by
business_type. --client filters to one (name or business_type).

Usage:
    python export_conversations.py --days 30 > audit.md
    python export_conversations.py --client "Acme Plumbing" --days 14 --format md
    python export_conversations.py --days 90 --format csv --out audit.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime
import io
import json
import sqlite3
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent


def load_env_key(key: str) -> str:
    env = PROJECT / ".env"
    if not env.exists():
        return ""
    with open(env, encoding="utf-8") as f:
        for line in f:
            if line.startswith(f"{key}="):
                return line.strip().split("=", 1)[1]
    return ""


def _utcnow() -> datetime.datetime:
    return datetime.datetime.utcnow()


def fetch(conn: sqlite3.Connection, since: datetime.datetime,
          client_filter: str | None) -> list[dict]:
    conn.row_factory = sqlite3.Row
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")
    q = """
        SELECT c.id, c.client_id, c.business_type, c.phone_number,
               c.state, c.branch, c.response_count, c.created_at,
               c.updated_at, c.metadata_json
        FROM conversations c WHERE c.created_at >= ?
    """
    params: list = [since_str]
    if client_filter:
        q += (" AND (c.business_type = ? OR c.client_id IN "
              "(SELECT id FROM clients WHERE name = ?))")
        params += [client_filter, client_filter]
    rows = conn.execute(q, params).fetchall()

    client_names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM clients")}

    def client_key(row) -> str:
        cid = row["client_id"]
        if cid and cid in client_names:
            return client_names[cid]
        return row["business_type"] or "unknown"

    out = []
    for row in rows:
        msgs = conn.execute(
            "SELECT role, content, created_at FROM messages "
            "WHERE conversation_id=? ORDER BY id", (row["id"],),
        ).fetchall()
        meta = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
        out.append({
            "conv_id": row["id"],
            "client": client_key(row),
            "phone": row["phone_number"] or "",
            "state": row["state"],
            "branch": row["branch"] or "-",
            "response_count": row["response_count"] or 0,
            "created": row["created_at"][:16],
            "customer_name": meta.get("customer_name", ""),
            "address": meta.get("address", ""),
            "appt_date": meta.get("appt_date", ""),
            "appt_time": meta.get("appt_time", ""),
            "service_type": meta.get("service_type", ""),
            "booked_event": meta.get("_booked_event", ""),
            "messages": [{"role": m["role"], "content": m["content"],
                          "at": m["created_at"][:16] if m["created_at"] else ""}
                         for m in msgs],
        })
    return out


def render_markdown(convs: list[dict]) -> str:
    if not convs:
        return "# Conversation Audit\n\n(no conversations in window)"
    lines = ["# Conversation Audit", ""]
    for c in convs:
        lines.append(f"## Conv #{c['conv_id']} — {c['client']} — {c['created']}")
        lines.append(f"- phone: {c['phone']} | state: {c['state']} | "
                     f"branch: {c['branch']} | responses: {c['response_count']}")
        booking = (
            f"customer={c['customer_name']} address={c['address']} "
            f"appt={c['appt_date']} {c['appt_time']} service={c['service_type']} "
            f"event={c['booked_event']}"
        )
        lines.append(f"- booking: {booking}")
        lines.append("")
        for m in c["messages"]:
            who = "🤖 AI" if m["role"] == "assistant" else (
                "👤 " if m["role"] == "user" else "⚙️ system")
            lines.append(f"{who} ({m['at']}): {m['content']}")
        lines.append("")
    return "\n".join(lines)


def render_csv(convs: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["conv_id", "client", "phone", "state", "branch", "responses",
                "created", "customer_name", "address", "appt_date", "appt_time",
                "service_type", "booked_event"])
    for c in convs:
        w.writerow([c["conv_id"], c["client"], c["phone"], c["state"],
                    c["branch"], c["response_count"], c["created"],
                    c["customer_name"], c["address"], c["appt_date"],
                    c["appt_time"], c["service_type"], c["booked_event"]])
    return buf.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--client", default=None,
                        help="filter to one client name/business_type")
    parser.add_argument("--format", choices=["md", "csv"], default="md")
    parser.add_argument("--out", default=None, help="write to file instead of stdout")
    args = parser.parse_args()

    db_path = load_env_key("DB_PATH") or str(PROJECT / "data" / "conversations.db")
    if not Path(db_path).exists():
        print("No conversations.db yet — nothing to export.")
        return 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    since = _utcnow() - datetime.timedelta(days=args.days)
    convs = fetch(conn, since, args.client)
    conn.close()

    text = render_markdown(convs) if args.format == "md" else render_csv(convs)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {len(convs)} conversations to {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
