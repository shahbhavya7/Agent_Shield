# AgentShield — Project Context

**Pitch:** Crash-test any AI agent before deployment. AgentShield generates adversarial
scenarios, injects faults mid-conversation, judges each answer, and — the differentiator —
for every failure produces a plain-English cause + a copy-pasteable fix backed by trace
evidence. Domain-independent; six sample domains ship with the repo (support, banking, HR,
insurance, airline, telecom), plus any third-party agent reachable over HTTP.

> This file is a one-page orientation, kept intentionally short. For the full architecture,
> every file's role, the data model, scoring math, and a judge Q&A, see
> `docs/PROJECT_GUIDE.md`. For how the system got here phase by phase, see `docs/PHASES.md`.

## Stack
- **Backend:** FastAPI + **PostgreSQL** + asyncio + **Temporal** (durable workflow
  execution). Entry: `backend/app/main.py`.
- **Frontend:** **Next.js** (App Router) + TypeScript + Tailwind (`frontend/`).
- **LLM:** OpenAI async client, isolated in `app/core/llm.py` so the provider is swappable.
  One key covers every LLM role: scenario generation, the Judge, and Explain+Fix.
- **Docker:** `docker-compose.yml` (repo root) containerizes the whole stack — postgres,
  temporal, backend, worker, all 6 sample agents, frontend — one container per process,
  mirroring `run_all.sh`'s process list. Env via `docker/.env` (copy from
  `docker/.env.example`; separate from `backend/.env`, which stays localhost-based for
  running outside Docker). Still **no** auth / Redis / Celery / websockets — those aren't
  needed either way.
- Two ways to run: `./run_all.sh` (native, localhost) or `docker compose up` (containerized).

## Pipeline
```
Connect agent → Verify connection → Agent knowledge → Generate test cases
                                                              │
                                                     Review test cases
                                                              │
                                                    Run reliability test (Temporal workflow)
                                                              │
                                              (pass) ──► Reliability report
                                              (fail) ──► Explain + suggest fix ──► report
```
Passing runs skip straight to the report; **only failures** go through Explain + suggest fix
(the coral differentiator — never cut it). Every run executes as a Temporal
`AgentTestWorkflow` (fanned out per agent under `RunGroupWorkflow` for a batch), not an
in-process background task — a run survives a backend restart.

## The one reconciliation
- **Six sample RAG agents (`backend/sample_rag_bot/` + `backend/sample_agents/*.py`, ports
  8002–8007) = targets we test.** Each is **fully standalone**: real LLM (gpt-4o-mini), own
  env/config, imports NOTHING from `app`. AgentShield reaches each **only over HTTP** via its
  `/chat` endpoint — exactly like any third-party agent. Each is a deliberate archetype
  (weak baseline, hardened baseline, invents-under-pressure, leaks-to-"staff", etc.) so
  demos exercise distinct, real failure signatures, not one scripted weakness repeated.
  Contract: `POST /chat {message, history, faults} -> {reply, trace}`. The `faults` field is
  part of these agents' own public API (we chose to expose it); a real external agent
  wouldn't, so AgentShield degrades to conversation-only there.
- **Deterministic bot (`backend/sample_bot/`, port 8001)** — kept in the repo but **NOT used**
  (decision: test only the RAG-style agents). Available only as an optional fallback later.
- **"Connect your own agent" (custom, black box)** = same pipeline but degrades gracefully:
  no injected agent-cooperative faults, trace limited to whatever the API returns. System
  faults (unreachable/500/timeout/malformed) and conversation testing (memory, injection,
  contradiction) still work fully against any endpoint.

## Data model — 9 PostgreSQL tables (`app/db.py`, no ORM, `init_schema()` idempotent)
- **agents**(id, name, kind[sample|custom], endpoint_url, auth_header, request_template, response_path, description, knowledge, knowledge_name, created_at)
- **customers**(id, name, created_at)
- **customer_agents**(id, customer_id, agent_id, created_at) — the actual testing context; a saved test-case library hangs off this row, not the agent
- **test_cases**(id, customer_agent_id, title, user_goal, test_type, assigned_fault, expected_behavior, seed_turns_json, source[ai|user], created_at) — the persistent, reviewable/editable library
- **run_groups**(id, created_at) — one row per "Run" click
- **runs**(id, agent_id, group_id, customer_agent_id, status[queued|running|done|error], reliability_score, breakdown_json, started_at, finished_at)
- **scenarios**(id, run_id, title, user_goal, test_type[support|memory|injection|contradiction|hallucination], assigned_fault[none|tool_timeout|stale_doc|injection], expected_behavior, seed_turns_json) — the frozen per-run execution copy of whatever suite ran
- **conversations**(id, run_id, scenario_id, verdict[pass|fail], severity[low|med|high], recovered, scores_json, explanation, suggested_fix, evidence, idem_key)
- **messages**(id, conversation_id, turn_index, role[tester|agent], content, trace_json)

## Build phases (see `docs/PHASES.md` for full detail)
1–4. Foundation, Run Engine, Analysis & Report, Frontend & Demo Safety — the original
hackathon MVP (SQLite, React+Vite). All superseded in part by 5–8 below.
5. **Multi-Customer & Test-Case Library** — Postgres, `customer_agents`/`test_cases`, the
   two-flow UI (Connect vs. Existing Agent Testing).
6. **Durable, Parallel Execution** — Temporal workflows replace the in-process background task.
7. **Reliability Hardening** — deterministic judge (fixed seed), derived severity, honest
   error states, persisted adaptive follow-ups.
8. **Sample Agent Roster** — 6 domains as deliberate pass/fail archetypes.

## Conventions
- All model calls go through `app.core.llm.chat()`; use `json_mode=True` for structured output.
- Config/secrets via `backend/.env` (gitignored) for native runs, `docker/.env` (gitignored)
  for `docker compose`; each has its own `.env.example` template.
- Sample RAG agents use a real LLM (gpt-4o-mini) — non-deterministic on purpose, so failures
  and recoveries emerge from genuine model behavior, not scripted rules.
