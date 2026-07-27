"""SQLite helpers + schema. Deliberately tiny — no ORM, no migrations (hackathon MVP)."""
import sqlite3
from datetime import datetime, timezone

from app.config import DB_PATH


def get_conn() -> sqlite3.Connection:
    """Return a connection with row access by column name and FK enforcement on."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_schema() -> None:
    """Create the 5 tables if they don't exist. Idempotent."""
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS agents (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            name              TEXT NOT NULL,
            kind              TEXT NOT NULL,          -- sample | custom
            endpoint_url      TEXT,
            auth_header       TEXT,
            request_template  TEXT,                   -- JSON string w/ {message},{history},{faults}
            response_path     TEXT,                   -- dot-path to reply in the response JSON
            description       TEXT,
            created_at        TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id         INTEGER NOT NULL REFERENCES agents(id),
            status           TEXT NOT NULL,           -- queued | running | done | error
            reliability_score REAL,
            breakdown_json   TEXT,
            started_at       TEXT,
            finished_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS scenarios (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id            INTEGER NOT NULL REFERENCES runs(id),
            title             TEXT,
            user_goal         TEXT,
            test_type         TEXT,   -- support | memory | injection | contradiction | hallucination
            assigned_fault    TEXT,   -- none | tool_timeout | stale_doc | injection
            expected_behavior TEXT,
            seed_turns_json   TEXT
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id         INTEGER NOT NULL REFERENCES runs(id),
            scenario_id    INTEGER NOT NULL REFERENCES scenarios(id),
            verdict        TEXT,           -- pass | fail | null(unjudged)
            severity       TEXT,           -- low | med | high
            recovered      INTEGER,        -- 0 | 1 | null
            scores_json    TEXT,
            explanation    TEXT,
            suggested_fix  TEXT,
            evidence       TEXT
        );

        CREATE TABLE IF NOT EXISTS messages (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id  INTEGER NOT NULL REFERENCES conversations(id),
            turn_index       INTEGER NOT NULL,
            role             TEXT NOT NULL,   -- tester | agent
            content          TEXT,
            trace_json       TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    print(f"[db] schema initialized at {DB_PATH}")


# ---------------------------------------------------------------------------
# Small query helpers (no ORM). Rows come back as sqlite3.Row (dict-like).
# ---------------------------------------------------------------------------
def get_agent(agent_id: int) -> sqlite3.Row | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    conn.close()
    return row


def get_agent_by_kind(kind: str) -> sqlite3.Row | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM agents WHERE kind = ? ORDER BY id LIMIT 1", (kind,)
    ).fetchone()
    conn.close()
    return row


def list_agents() -> list[sqlite3.Row]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM agents ORDER BY id").fetchall()
    conn.close()
    return rows


def insert_agent(
    name: str,
    kind: str,
    endpoint_url: str,
    response_path: str,
    request_template: str,
    auth_header: str | None = None,
    description: str | None = None,
) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO agents
           (name, kind, endpoint_url, auth_header, request_template,
            response_path, description, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (name, kind, endpoint_url, auth_header, request_template,
         response_path, description, now_iso()),
    )
    conn.commit()
    agent_id = cur.lastrowid
    conn.close()
    return agent_id


def update_agent_description(agent_id: int, description: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE agents SET description = ? WHERE id = ?", (description, agent_id))
    conn.commit()
    conn.close()


import json as _json


def insert_run(agent_id: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO runs (agent_id, status, started_at) VALUES (?, 'running', ?)",
        (agent_id, now_iso()),
    )
    conn.commit()
    run_id = cur.lastrowid
    conn.close()
    return run_id


def update_run(
    run_id: int,
    status: str | None = None,
    reliability_score: float | None = None,
    breakdown_json: str | None = None,
    finished: bool = False,
) -> None:
    sets, vals = [], []
    if status is not None:
        sets.append("status = ?"); vals.append(status)
    if reliability_score is not None:
        sets.append("reliability_score = ?"); vals.append(reliability_score)
    if breakdown_json is not None:
        sets.append("breakdown_json = ?"); vals.append(breakdown_json)
    if finished:
        sets.append("finished_at = ?"); vals.append(now_iso())
    if not sets:
        return
    vals.append(run_id)
    conn = get_conn()
    conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    conn.close()


def get_run(run_id: int) -> sqlite3.Row | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    return row


def insert_scenario(run_id: int, s: dict) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO scenarios
           (run_id, title, user_goal, test_type, assigned_fault,
            expected_behavior, seed_turns_json)
           VALUES (?,?,?,?,?,?,?)""",
        (run_id, s.get("title"), s.get("user_goal"), s.get("test_type"),
         s.get("assigned_fault"), s.get("expected_behavior"),
         _json.dumps(s.get("seed_turns", []))),
    )
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return sid


def insert_conversation(run_id: int, scenario_id: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO conversations (run_id, scenario_id) VALUES (?, ?)",
        (run_id, scenario_id),
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid


def insert_message(
    conversation_id: int, turn_index: int, role: str, content: str, trace: dict | None
) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO messages (conversation_id, turn_index, role, content, trace_json)
           VALUES (?,?,?,?,?)""",
        (conversation_id, turn_index, role, content,
         _json.dumps(trace) if trace is not None else None),
    )
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return mid


def get_scenario(scenario_id: int) -> sqlite3.Row | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
    conn.close()
    return row


def get_conversation(conversation_id: int) -> sqlite3.Row | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    conn.close()
    return row


def get_conversations_for_run(run_id: int) -> list[sqlite3.Row]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM conversations WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    conn.close()
    return rows


def get_messages(conversation_id: int) -> list[sqlite3.Row]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY turn_index, id",
        (conversation_id,),
    ).fetchall()
    conn.close()
    return rows


def update_conversation_verdict(
    conversation_id: int,
    verdict: str,
    severity: str | None,
    recovered: bool | None,
    scores: dict | None,
    evidence: str | None,
) -> None:
    conn = get_conn()
    conn.execute(
        """UPDATE conversations
           SET verdict=?, severity=?, recovered=?, scores_json=?, evidence=?
           WHERE id=?""",
        (verdict, severity,
         (None if recovered is None else (1 if recovered else 0)),
         _json.dumps(scores) if scores is not None else None,
         evidence, conversation_id),
    )
    conn.commit()
    conn.close()


def update_conversation_fix(
    conversation_id: int, explanation: str, suggested_fix: str, evidence: str | None
) -> None:
    conn = get_conn()
    if evidence is not None:
        conn.execute(
            "UPDATE conversations SET explanation=?, suggested_fix=?, evidence=? WHERE id=?",
            (explanation, suggested_fix, evidence, conversation_id),
        )
    else:
        conn.execute(
            "UPDATE conversations SET explanation=?, suggested_fix=? WHERE id=?",
            (explanation, suggested_fix, conversation_id),
        )
    conn.commit()
    conn.close()


def build_conversation_payload(conversation_id: int) -> dict | None:
    """Assemble one conversation for the report: scenario meta + scores + messages+trace."""
    conv = get_conversation(conversation_id)
    if conv is None:
        return None
    conv = dict(conv)
    scenario = get_scenario(conv["scenario_id"])
    scenario = dict(scenario) if scenario else {}

    messages = []
    for m in get_messages(conversation_id):
        m = dict(m)
        trace = None
        if m.get("trace_json"):
            try:
                trace = _json.loads(m["trace_json"])
            except (_json.JSONDecodeError, TypeError):
                trace = None
        messages.append({
            "turn_index": m["turn_index"], "role": m["role"],
            "content": m["content"], "trace": trace,
        })

    scores = None
    if conv.get("scores_json"):
        try:
            scores = _json.loads(conv["scores_json"])
        except (_json.JSONDecodeError, TypeError):
            scores = None

    return {
        "id": conv["id"],
        "scenario_title": scenario.get("title"),
        "user_goal": scenario.get("user_goal"),
        "test_type": scenario.get("test_type"),
        "assigned_fault": scenario.get("assigned_fault"),
        "expected_behavior": scenario.get("expected_behavior"),
        "verdict": conv.get("verdict"),
        "severity": conv.get("severity"),
        "recovered": (None if conv.get("recovered") is None else bool(conv["recovered"])),
        "scores": scores,
        "explanation": conv.get("explanation"),
        "suggested_fix": conv.get("suggested_fix"),
        "evidence": conv.get("evidence"),
        "messages": messages,
    }


def run_counts(run_id: int) -> dict:
    conn = get_conn()
    scen = conn.execute("SELECT COUNT(*) c FROM scenarios WHERE run_id=?", (run_id,)).fetchone()["c"]
    convs = conn.execute("SELECT COUNT(*) c FROM conversations WHERE run_id=?", (run_id,)).fetchone()["c"]
    msgs = conn.execute(
        "SELECT COUNT(*) c FROM messages m JOIN conversations c ON m.conversation_id=c.id WHERE c.run_id=?",
        (run_id,),
    ).fetchone()["c"]
    judged = conn.execute(
        "SELECT COUNT(*) c FROM conversations WHERE run_id=? AND verdict IS NOT NULL", (run_id,)
    ).fetchone()["c"]
    conn.close()
    return {"scenarios": scen, "conversations": convs, "messages": msgs, "judged": judged}


if __name__ == "__main__":
    init_schema()
