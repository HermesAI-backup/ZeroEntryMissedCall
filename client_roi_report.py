"""Per-client ROI report — the missed-call funnel with attributed revenue.

Mirrors what HighLevel reports for agencies (missed calls -> text-backs ->
replies -> bookings -> dollar value), from OUR data so it doubles as the
billing reconciliation for the 10%-of-booked-jobs model (Service Agreement
§4: a job is attributed if the customer's number appears in a conversation
and the job is booked within 30 days).

Funnel definitions (windowed by --days, default 30):
  missed_calls   — conversations whose first system message is "Missed call
                   from ..." (true missed-call source, not direct text-in)
  texted         — conversations where the AI sent >=1 message
                   (response_count >= 1)
  replied        — conversations with >=1 customer (user) message
  booked         — branch == 'booked' OR metadata has _booked_event
  revenue est    — booked x avg job value (--avg-job-value or env
                   AVG_JOB_VALUE); zero job value = bookings only. The real
                   attributed base is the client's billed amount (we can't
                   see actual invoices without read-only payment access), so
                   this is an ESTIMATE for the pitch, not a bill.

Grouping: real clients (clients table) by name; legacy conversations without
client_id group by business_type.

Usage:
    python client_roi_report.py                          # all, last 30 days
    python client_roi_report.py --days 7                 # last 7 days
    python client_roi_report.py --client "Acme Plumbing" # one client
    python client_roi_report.py --avg-job-value 350      # revenue estimate
    python client_roi_report.py --json                   # machine-readable
"""
from __future__ import annotations

import argparse
import datetime
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


def build_funnel(conn: sqlite3.Connection, since: datetime.datetime,
                 client_filter: str | None = None) -> list[dict]:
    """Compute the per-client funnel over conversations created >= since.

    Pure reads; returns a list of client dicts with funnel counts + rates.
    """
    # SQLAlchemy stores DateTime as 'YYYY-MM-DD HH:MM:SS.ffffff' — match that
    # format for the >= comparison (ISO 'T' would lexicographically exclude
    # same-day rows: ' ' < 'T').
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")
    conn.row_factory = sqlite3.Row

    q = """
        SELECT c.id, c.client_id, c.business_type, c.state, c.branch,
               c.response_count, c.metadata_json
        FROM conversations c
        WHERE c.created_at >= ?
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

    funnel: dict[str, dict] = {}
    for row in rows:
        k = client_key(row)
        f = funnel.setdefault(k, {"client": k, "missed_calls": 0, "texted": 0,
                                  "replied": 0, "booked": 0})
        if (row["response_count"] or 0) >= 1:
            f["texted"] += 1
        meta = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
        if row["branch"] == "booked" or meta.get("_booked_event"):
            f["booked"] += 1
        m = conn.execute(
            "SELECT 1 FROM messages WHERE conversation_id=? "
            "AND role='system' AND content LIKE 'Missed call from%' LIMIT 1",
            (row["id"],),
        ).fetchone()
        if m:
            f["missed_calls"] += 1
        m2 = conn.execute(
            "SELECT 1 FROM messages WHERE conversation_id=? AND role='user' LIMIT 1",
            (row["id"],),
        ).fetchone()
        if m2:
            f["replied"] += 1

    for f in funnel.values():
        f["reply_rate"] = (f["replied"] / f["texted"]) if f["texted"] else 0.0
        f["booking_rate"] = (f["booked"] / f["texted"]) if f["texted"] else 0.0
    return sorted(funnel.values(), key=lambda f: (f["booked"], f["texted"]), reverse=True)


def render_table(funnel: list[dict], avg_job_value: float) -> str:
    lines = [
        f"{'Client':<22} {'Missed':>7} {'Texted':>7} {'Replied':>7} {'Booked':>7} "
        f"{'Reply%':>8} {'Book%':>8} {'Rev$ est':>10} {'10% cut':>8}"
    ]
    lines.append("-" * len(lines[0]))
    for f in funnel:
        rev = f["booked"] * avg_job_value if avg_job_value else 0.0
        lines.append(
            f"{f['client'][:22]:<22} {f['missed_calls']:>7} {f['texted']:>7} "
            f"{f['replied']:>7} {f['booked']:>7} "
            f"{f['reply_rate'] * 100:>7.0f}% {f['booking_rate'] * 100:>7.0f}% "
            f"{rev:>10,.0f} {rev * 0.10:>8,.0f}"
        )
    if not funnel:
        lines.append("(no conversations in window)")
    if avg_job_value:
        lines.append("")
        lines.append("Revenue estimate: booked x $" f"{avg_job_value:,.0f} avg job value "
                     "(--avg-job-value). Real attributed base = client's billed amount.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--client", default=None,
                        help="filter to one client name/business_type")
    parser.add_argument("--avg-job-value", type=float, default=None,
                        help="avg $ per booked job for the revenue estimate "
                             "(env AVG_JOB_VALUE also works)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    env_avg = load_env_key("AVG_JOB_VALUE")
    avg_job_value = args.avg_job_value if args.avg_job_value is not None else (
        float(env_avg) if env_avg else 0.0
    )

    db_path = load_env_key("DB_PATH") or str(PROJECT / "data" / "conversations.db")
    if not Path(db_path).exists():
        print("No conversations.db yet — nothing to report.")
        return 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    since = _utcnow() - datetime.timedelta(days=args.days)
    result = build_funnel(conn, since, args.client)
    conn.close()

    if args.json:
        print(json.dumps({"window_days": args.days, "avg_job_value": avg_job_value,
                          "clients": result}, indent=2))
    else:
        print(render_table(result, avg_job_value))
    return 0


if __name__ == "__main__":
    sys.exit(main())
