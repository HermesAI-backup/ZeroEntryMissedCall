"""Regression test: multi-client foundation (clients table + migration + seed).

- clients table exists with the per-client columns
- conversations.client_id migration applied
- the dogfood "Default (env mirror)" client is seeded and idempotent
- the seed mirrors current .env settings

Usage: python test_clients.py   (plain asserts, exit 0/1)
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).parent
sys.path.insert(0, str(PROJECT))

import sqlite3  # noqa: E402

from database import Client, ensure_default_client, get_session, init_db  # noqa: E402
from config import get_settings  # noqa: E402

DB_PATH = PROJECT / "data" / "conversations.db"


def _cols(table: str) -> list[str]:
    return [r[1] for r in sqlite3.connect(DB_PATH).execute(f"PRAGMA table_info({table})")]


def test_clients_table_exists() -> None:
    cols = _cols("clients")
    for c in ("id", "name", "business_type", "business_name", "service_area",
              "review_link", "telnyx_number", "owner_phone", "calendar_id",
              "spreadsheet_id", "messaging_profile_id", "texml_app_id",
              "campaign_id", "business_hours", "active"):
        assert c in cols, f"missing clients.{c}"


def test_conversations_have_client_id() -> None:
    assert "client_id" in _cols("conversations")


def test_default_client_seeded_and_idempotent() -> None:
    init_db()
    id1 = ensure_default_client()
    id2 = ensure_default_client()  # second call must upsert, not duplicate
    db = get_session()
    try:
        rows = db.query(Client).filter(Client.name == "Default (env mirror)").all()
        assert len(rows) == 1, f"expected 1 seed row, got {len(rows)}"
        assert rows[0].id == id1 == id2
    finally:
        db.close()


def test_seed_mirrors_env() -> None:
    s = get_settings()
    db = get_session()
    try:
        client = db.query(Client).filter(Client.name == "Default (env mirror)").first()
        assert client.business_type == s.business_type
        assert client.business_name == s.business_name
        assert client.telnyx_number == s.telnyx_from
    finally:
        db.close()


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"PASS: {len(tests)} multi-client foundation checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
