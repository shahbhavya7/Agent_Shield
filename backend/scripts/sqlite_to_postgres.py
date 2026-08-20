"""One-time copy of the old SQLite data into PostgreSQL.

The backend moved from backend/agentshield.db to PostgreSQL; this carries the existing
runs/reports over so replay keeps working. Run once, from backend/:

    python -m scripts.sqlite_to_postgres

Safe to skip entirely if you don't care about the old runs. It refuses to run if the
target tables already hold rows, so it can't double-import.
"""
import os
import sqlite3
import sys

from app.db import get_conn, init_schema

SQLITE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agentshield.db")

# Copied parent-first so foreign keys resolve.
TABLES = {
    "agents": ["id", "name", "kind", "endpoint_url", "auth_header", "request_template",
               "response_path", "description", "created_at"],
    "runs": ["id", "agent_id", "status", "reliability_score", "breakdown_json",
             "started_at", "finished_at"],
    "scenarios": ["id", "run_id", "title", "user_goal", "test_type", "assigned_fault",
                  "expected_behavior", "seed_turns_json"],
    "conversations": ["id", "run_id", "scenario_id", "verdict", "severity", "recovered",
                      "scores_json", "explanation", "suggested_fix", "evidence"],
    "messages": ["id", "conversation_id", "turn_index", "role", "content", "trace_json"],
}


def main() -> int:
    if not os.path.exists(SQLITE_PATH):
        print(f"no SQLite file at {SQLITE_PATH} — nothing to copy")
        return 0

    init_schema()
    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row
    dst = get_conn()

    for table in TABLES:
        existing = dst.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        if existing:
            print(f"abort: postgres table '{table}' already has {existing} rows — refusing to re-import")
            return 1

    for table, cols in TABLES.items():
        rows = src.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY id").fetchall()
        if not rows:
            continue
        placeholders = ",".join(["%s"] * len(cols))
        dst.cursor().executemany(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
            [tuple(r[c] for c in cols) for r in rows],
        )
        # Explicit ids were inserted, so push each SERIAL sequence past them.
        dst.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
        )
        print(f"copied {len(rows):>4} rows -> {table}")

    dst.commit()
    dst.close()
    src.close()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
