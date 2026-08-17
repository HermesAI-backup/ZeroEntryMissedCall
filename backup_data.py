"""Backup the system of record (conversations.db + .env) to OneDrive.

DB uses the SQLite backup API for a consistent snapshot even while the server
is running (WAL-safe). Keeps the newest KEEP snapshots, prunes the rest.

Silent-ish: prints one confirmation line (cron delivers it daily).
Exit 0 on success, 1 on failure (cron alerts on non-zero exit).
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
DB = PROJECT / "data" / "conversations.db"
ENV = PROJECT / ".env"
DEST_ROOT = Path.home() / "OneDrive" / "Backups" / "missed-call-ai"
KEEP = 14


def main() -> int:
    try:
        DEST_ROOT.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = DEST_ROOT / stamp
        dest.mkdir(exist_ok=True)

        if not DB.exists():
            raise FileNotFoundError(f"missing {DB}")
        # Consistent snapshot via SQLite backup API (safe with live writes/WAL)
        with sqlite3.connect(DB) as src, sqlite3.connect(dest / "conversations.db") as out:
            src.backup(out)

        if not ENV.exists():
            raise FileNotFoundError(f"missing {ENV}")
        shutil.copy2(ENV, dest / ".env")

        # Prune old snapshots
        snaps = sorted([p for p in DEST_ROOT.iterdir() if p.is_dir()], reverse=True)
        for old in snaps[KEEP:]:
            shutil.rmtree(old, ignore_errors=True)

        db_size = (dest / "conversations.db").stat().st_size // 1024
        print(f"Backup OK: {dest} (db {db_size} KB, {len(snaps)} snapshots kept)")
        return 0
    except Exception as e:
        print(f"Backup FAILED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
