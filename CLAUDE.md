# AgentShield — Project Context

**Pitch:** Crash-test any AI agent before deployment. AgentShield generates adversarial
scenarios, injects faults mid-conversation, judges each answer, and — the differentiator —
for every failure produces a plain-English cause + a copy-pasteable fix backed by trace
evidence. Domain-independent; support today, banking/HR next.

## Stack
- **Backend:** FastAPI + SQLite + asyncio. Entry: `backend/app/main.py`.
- **Frontend:** React + Vite + TypeScript (`frontend/`).
- **LLM:** OpenAI async client, isolated in `app/core/llm.py` so the provider is swappable.
  One key covers all three LLM roles: scenario generation, the Judge, and Explain+Fix.
- **No** Docker / auth / Postgres / Redis / Celery / websockets. Hackathon MVP — do not over-engineer.

## Pipeline
```
Start → Generate scenarios → Break agent as it runs (faults + trace) → Judge each answer
                                                                              │
                                                        (pass) ──► Reliability report
                                                        (fail) ──► Explain + suggest fix ──► report
```
Passing runs skip straight to the report; **only failures** go through Explain + suggest fix
(the coral differentiator — never cut it).

## The one reconciliation
- **RAG customer-support agent (`backend/sample_rag_bot/`, port 8002) = THE target we test.**
  A **fully standalone** service: real LLM (gpt-4o-mini), own env/config, imports NOTHING from
  `app`. AgentShield reaches it **only over HTTP** via its `/chat` endpoint — exactly like any
  third-party agent. A genuine small RAG agent (retrieve docs → grounded prompt → OpenAI);
  non-deterministic, failures/recoveries emerge from the real model. Contract:
  `POST /chat {message, history, faults} -> {reply, trace}`. The `faults` field is part of this
  agent's own public API (we chose to expose it); a real external agent wouldn't, so AgentShield
  degrades to conversation-only there.
- **Deterministic bot (`backend/sample_bot/`, port 8001)** — kept in the repo but **NOT used**
  (decision: use only the RAG bot). Available only as an optional demo-safe fallback later.
- **"Connect your own agent" (custom, black box)** = same pipeline but degrades gracefully:
  no injected faults, trace limited to whatever the API returns. Conversation testing
  (memory, injection, contradiction) still works fully.

> Phase 2 note: seed ONLY the RAG bot as the sample `agents` row (kind=sample).

## Data model — 5 SQLite tables (`app/db.py`)
- **agents**(id, name, kind[sample|custom], endpoint_url, auth_header, request_template, response_path, description, created_at)
- **runs**(id, agent_id, status[queued|running|done|error], reliability_score, breakdown_json, started_at, finished_at)
- **scenarios**(id, run_id, title, user_goal, test_type[support|memory|injection|contradiction|hallucination], assigned_fault[none|tool_timeout|stale_doc|injection], expected_behavior, seed_turns_json)
- **conversations**(id, run_id, scenario_id, verdict[pass|fail], severity[low|med|high], recovered, scores_json, explanation, suggested_fix, evidence)
- **messages**(id, conversation_id, turn_index, role[tester|agent], content, trace_json)

## Build phases
1. **Foundation & Sample Bot** — stack up + controllable target bot with 3 fault hooks + trace.
2. **Run Engine** — HTTP adapter + scenario generation + runner (fault injection + trace recording).
3. **Analysis & Report** — Judge + Explain/Fix (coral) + scoring + report/replay/agents API.
4. **Frontend & Demo Safety** — pick/run → dashboard → coral report → replay + pre-baked fallback.

## Conventions
- Sample bot uses **NO LLM** — deterministic rules, reproducible, free.
- All model calls go through `app.core.llm.chat()`; use `json_mode=True` for structured output.
- Config/secrets via `backend/.env` (gitignored); `.env.example` is the template.
