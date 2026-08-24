"""PostgreSQL helpers + schema. Deliberately tiny — no ORM, no migrations (hackathon MVP).

`init_schema()` is the single, idempotent schema-creation entry point; it runs on FastAPI
startup. Rows come back as plain dicts (psycopg `dict_row`), so callers use row["col"].
"""
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from app.config import DATABASE_URL


# Customer name used for agents connected ad-hoc, with no customer selected.
UNASSIGNED_CUSTOMER = "Unassigned"


def get_conn() -> psycopg.Connection:
    """Return a connection whose rows are dicts (access by column name)."""
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_schema() -> None:
    """Create the tables if they don't exist. Idempotent."""
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS agents (
            id                SERIAL PRIMARY KEY,
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
            id               SERIAL PRIMARY KEY,
            agent_id         INTEGER NOT NULL REFERENCES agents(id),
            status           TEXT NOT NULL,           -- queued | running | done | error
            reliability_score DOUBLE PRECISION,
            breakdown_json   TEXT,
            started_at       TEXT,
            finished_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS scenarios (
            id                SERIAL PRIMARY KEY,
            run_id            INTEGER NOT NULL REFERENCES runs(id),
            title             TEXT,
            user_goal         TEXT,
            test_type         TEXT,   -- support | memory | injection | contradiction | hallucination
            assigned_fault    TEXT,   -- none | tool_timeout | stale_doc | injection
            expected_behavior TEXT,
            seed_turns_json   TEXT
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id             SERIAL PRIMARY KEY,
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
            id               SERIAL PRIMARY KEY,
            conversation_id  INTEGER NOT NULL REFERENCES conversations(id),
            turn_index       INTEGER NOT NULL,
            role             TEXT NOT NULL,   -- tester | agent
            content          TEXT,
            trace_json       TEXT
        );

        -- Customer -> agent -> test cases. A customer_agents row IS the testing context:
        -- the same agent onboarded for two customers is two rows, tested separately.
        CREATE TABLE IF NOT EXISTS customers (
            id          SERIAL PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS customer_agents (
            id           SERIAL PRIMARY KEY,
            customer_id  INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            agent_id     INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            created_at   TEXT NOT NULL,
            UNIQUE (customer_id, agent_id)
        );

        -- The persistent test-case library for one customer-agent combination.
        -- Same columns as `scenarios` (which stays the per-run execution copy).
        CREATE TABLE IF NOT EXISTS test_cases (
            id                SERIAL PRIMARY KEY,
            customer_agent_id INTEGER NOT NULL REFERENCES customer_agents(id) ON DELETE CASCADE,
            title             TEXT,
            user_goal         TEXT,
            test_type         TEXT,   -- support | memory | injection | contradiction | hallucination
            assigned_fault    TEXT,   -- none | tool_timeout | stale_doc | injection | api_*
            expected_behavior TEXT,
            seed_turns_json   TEXT,
            source            TEXT,   -- ai | user
            created_at        TEXT NOT NULL
        );

        -- One "Run Selected Agents" click = one run_groups row holding N runs, one per
        -- selected agent. Each run keeps its own scenarios, conversations and score, so
        -- agents stay independently reportable and comparable.
        CREATE TABLE IF NOT EXISTS run_groups (
            id          SERIAL PRIMARY KEY,
            created_at  TEXT NOT NULL
        );

        ALTER TABLE runs ADD COLUMN IF NOT EXISTS group_id
            INTEGER REFERENCES run_groups(id);
        ALTER TABLE runs ADD COLUMN IF NOT EXISTS customer_agent_id
            INTEGER REFERENCES customer_agents(id);

        -- The agent's own docs, uploaded on the Connect step. Stored so every later
        -- run for this agent stays grounded without re-uploading the file.
        ALTER TABLE agents ADD COLUMN IF NOT EXISTS knowledge TEXT;
        ALTER TABLE agents ADD COLUMN IF NOT EXISTS knowledge_name TEXT;
        """
    )
    conn.commit()
    conn.close()
    print("[db] schema initialized")


# ---------------------------------------------------------------------------
# Small query helpers (no ORM). Rows come back as dicts.
# ---------------------------------------------------------------------------
def get_agent(agent_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM agents WHERE id = %s", (agent_id,)).fetchone()
    conn.close()
    return row


def get_agent_by_kind(kind: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM agents WHERE kind = %s ORDER BY id LIMIT 1", (kind,)
    ).fetchone()
    conn.close()
    return row


def list_agents() -> list[dict]:
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
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (name, kind, endpoint_url, auth_header, request_template,
         response_path, description, now_iso()),
    )
    agent_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return agent_id


def update_agent_description(agent_id: int, description: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE agents SET description = %s WHERE id = %s", (description, agent_id))
    conn.commit()
    conn.close()


def set_agent_knowledge(agent_id: int, knowledge: str, knowledge_name: str | None = None) -> None:
    """Attach the uploaded docs to the agent so later runs can ground themselves on them."""
    conn = get_conn()
    conn.execute(
        "UPDATE agents SET knowledge = %s, knowledge_name = %s WHERE id = %s",
        (knowledge, knowledge_name, agent_id),
    )
    conn.commit()
    conn.close()


import json as _json


def insert_run_group() -> int:
    """Create the batch that a set of same-click runs belong to."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO run_groups (created_at) VALUES (%s) RETURNING id", (now_iso(),)
    )
    gid = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return gid


def insert_run(
    agent_id: int,
    group_id: int | None = None,
    customer_agent_id: int | None = None,
) -> int:
    """One run against one agent. `group_id` ties it to the batch it was launched with."""
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO runs (agent_id, status, started_at, group_id, customer_agent_id)
           VALUES (%s, 'running', %s, %s, %s) RETURNING id""",
        (agent_id, now_iso(), group_id, customer_agent_id),
    )
    run_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return run_id


def get_runs_for_group(group_id: int) -> list[dict]:
    """Every run in a batch, oldest first, with the agent and customer names joined in.

    Ordered by id so the response lines up with the order the client sent its targets.
    """
    conn = get_conn()
    rows = conn.execute(
        """SELECT r.*, a.name AS agent_name, c.name AS customer_name
           FROM runs r
           JOIN agents a               ON a.id = r.agent_id
           LEFT JOIN customer_agents ca ON ca.id = r.customer_agent_id
           LEFT JOIN customers c        ON c.id = ca.customer_id
           WHERE r.group_id = %s
           ORDER BY r.id""",
        (group_id,),
    ).fetchall()
    conn.close()
    return rows


def run_group_exists(group_id: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT id FROM run_groups WHERE id = %s", (group_id,)).fetchone()
    conn.close()
    return row is not None


def update_run(
    run_id: int,
    status: str | None = None,
    reliability_score: float | None = None,
    breakdown_json: str | None = None,
    finished: bool = False,
) -> None:
    sets, vals = [], []
    if status is not None:
        sets.append("status = %s"); vals.append(status)
    if reliability_score is not None:
        sets.append("reliability_score = %s"); vals.append(reliability_score)
    if breakdown_json is not None:
        sets.append("breakdown_json = %s"); vals.append(breakdown_json)
    if finished:
        sets.append("finished_at = %s"); vals.append(now_iso())
    if not sets:
        return
    vals.append(run_id)
    conn = get_conn()
    conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = %s", vals)
    conn.commit()
    conn.close()


def get_run(run_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM runs WHERE id = %s", (run_id,)).fetchone()
    conn.close()
    return row


def insert_scenario(run_id: int, s: dict) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO scenarios
           (run_id, title, user_goal, test_type, assigned_fault,
            expected_behavior, seed_turns_json)
           VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (run_id, s.get("title"), s.get("user_goal"), s.get("test_type"),
         s.get("assigned_fault"), s.get("expected_behavior"),
         _json.dumps(s.get("seed_turns", []))),
    )
    sid = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return sid


def insert_conversation(run_id: int, scenario_id: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO conversations (run_id, scenario_id) VALUES (%s, %s) RETURNING id",
        (run_id, scenario_id),
    )
    cid = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return cid


def insert_message(
    conversation_id: int, turn_index: int, role: str, content: str, trace: dict | None
) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO messages (conversation_id, turn_index, role, content, trace_json)
           VALUES (%s,%s,%s,%s,%s) RETURNING id""",
        (conversation_id, turn_index, role, content,
         _json.dumps(trace) if trace is not None else None),
    )
    mid = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return mid


def get_scenario(scenario_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM scenarios WHERE id = %s", (scenario_id,)).fetchone()
    conn.close()
    return row


def get_conversation(conversation_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM conversations WHERE id = %s", (conversation_id,)).fetchone()
    conn.close()
    return row


def get_conversations_for_run(run_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM conversations WHERE run_id = %s ORDER BY id", (run_id,)
    ).fetchall()
    conn.close()
    return rows


def get_messages(conversation_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id = %s ORDER BY turn_index, id",
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
           SET verdict=%s, severity=%s, recovered=%s, scores_json=%s, evidence=%s
           WHERE id=%s""",
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
            "UPDATE conversations SET explanation=%s, suggested_fix=%s, evidence=%s WHERE id=%s",
            (explanation, suggested_fix, evidence, conversation_id),
        )
    else:
        conn.execute(
            "UPDATE conversations SET explanation=%s, suggested_fix=%s WHERE id=%s",
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
    scen = conn.execute("SELECT COUNT(*) c FROM scenarios WHERE run_id=%s", (run_id,)).fetchone()["c"]
    convs = conn.execute("SELECT COUNT(*) c FROM conversations WHERE run_id=%s", (run_id,)).fetchone()["c"]
    msgs = conn.execute(
        "SELECT COUNT(*) c FROM messages m JOIN conversations c ON m.conversation_id=c.id WHERE c.run_id=%s",
        (run_id,),
    ).fetchone()["c"]
    judged = conn.execute(
        "SELECT COUNT(*) c FROM conversations WHERE run_id=%s AND verdict IS NOT NULL", (run_id,)
    ).fetchone()["c"]
    conn.close()
    return {"scenarios": scen, "conversations": convs, "messages": msgs, "judged": judged}


# ---------------------------------------------------------------------------
# Customer -> agent -> test cases.
# ---------------------------------------------------------------------------
def get_or_create_customer(name: str) -> int:
    """Customer id for `name`, inserting the row the first time. Idempotent."""
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO customers (name, created_at) VALUES (%s, %s)
           ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
           RETURNING id""",
        (name, now_iso()),
    )
    cid = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return cid


def get_agent_by_name(name: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM agents WHERE name = %s ORDER BY id LIMIT 1", (name,)
    ).fetchone()
    conn.close()
    return row


def get_or_create_customer_agent(customer_id: int, agent_id: int) -> int:
    """Id of the customer-agent testing context, inserting it the first time.

    This row — not the agent — is what test cases hang off, so the same agent
    onboarded for two customers stays two separate contexts.
    """
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO customer_agents (customer_id, agent_id, created_at)
           VALUES (%s, %s, %s)
           ON CONFLICT (customer_id, agent_id) DO UPDATE SET customer_id = EXCLUDED.customer_id
           RETURNING id""",
        (customer_id, agent_id, now_iso()),
    )
    caid = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return caid


def list_customer_agents() -> list[dict]:
    """Every customer-agent combination, with the customer and agent names joined in."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT ca.id, ca.customer_id, ca.agent_id,
                  c.name AS customer_name, a.name AS agent_name
           FROM customer_agents ca
           JOIN customers c ON c.id = ca.customer_id
           JOIN agents a    ON a.id = ca.agent_id
           ORDER BY c.id, a.id"""
    ).fetchall()
    conn.close()
    return rows


def insert_test_case(customer_agent_id: int, tc: dict, source: str = "ai") -> int:
    """Store one generated/edited test case against a customer-agent combination."""
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO test_cases
           (customer_agent_id, title, user_goal, test_type, assigned_fault,
            expected_behavior, seed_turns_json, source, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (customer_agent_id, tc.get("title"), tc.get("user_goal"), tc.get("test_type"),
         tc.get("assigned_fault"), tc.get("expected_behavior"),
         _json.dumps(tc.get("seed_turns", [])), source, now_iso()),
    )
    tid = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return tid


def get_customer_agent(customer_agent_id: int) -> dict | None:
    """One customer-agent combination with the customer and agent names joined in."""
    conn = get_conn()
    row = conn.execute(
        """SELECT ca.id, ca.customer_id, ca.agent_id,
                  c.name AS customer_name, a.name AS agent_name
           FROM customer_agents ca
           JOIN customers c ON c.id = ca.customer_id
           JOIN agents a    ON a.id = ca.agent_id
           WHERE ca.id = %s""",
        (customer_agent_id,),
    ).fetchone()
    conn.close()
    return row


def default_customer_agent(agent_id: int) -> int:
    """The customer-agent context for an agent connected ad-hoc (no customer chosen).

    New agents from the "Connect Your AI Agent" flow have no customer yet, but test cases
    hang off a customer_agents row — so they go under a reserved UNASSIGNED_CUSTOMER.
    Keeps one storage path for both flows.
    """
    return get_or_create_customer_agent(get_or_create_customer(UNASSIGNED_CUSTOMER), agent_id)


def replace_test_cases(customer_agent_id: int, cases: list[dict], sources: list[str] | None = None) -> int:
    """Make `cases` the stored suite for THIS combination only. Returns how many were saved.

    Scoped by customer_agent_id, so saving Customer 1 / NorthBank never touches
    Customer 2 / NorthBank. Replaces in one transaction.
    """
    conn = get_conn()
    conn.execute("DELETE FROM test_cases WHERE customer_agent_id = %s", (customer_agent_id,))
    ts = now_iso()
    for i, tc in enumerate(cases):
        source = (sources[i] if sources and i < len(sources) else None) or "ai"
        conn.execute(
            """INSERT INTO test_cases
               (customer_agent_id, title, user_goal, test_type, assigned_fault,
                expected_behavior, seed_turns_json, source, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (customer_agent_id, tc.get("title"), tc.get("user_goal"), tc.get("test_type"),
             tc.get("assigned_fault"), tc.get("expected_behavior"),
             _json.dumps(tc.get("seed_turns", [])), source, ts),
        )
    conn.commit()
    conn.close()
    return len(cases)


def get_test_cases(customer_agent_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM test_cases WHERE customer_agent_id = %s ORDER BY id",
        (customer_agent_id,),
    ).fetchall()
    conn.close()
    return rows


if __name__ == "__main__":
    init_schema()
