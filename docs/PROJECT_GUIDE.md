# AgentShield — Complete Project Guide

> **Read this to understand, present, and defend the whole project.**
> It explains what AgentShield is, how every piece works, the exact end-to-end flow,
> and a judge Q&A. Nothing here is aspirational — it all describes code that exists in this repo.

---

## 1. The one-sentence pitch

**AgentShield crash-tests any AI agent through its API before you ship it** — it generates
adversarial scenarios, injects real faults mid-conversation, judges every answer, and for each
failure hands you a plain-English cause **plus a copy-pasteable fix** backed by trace evidence.

**Tagline:** *Before you deploy your AI agent, crash-test it.*

---

## 2. The problem we solve

Teams ship LLM agents (support bots, RAG assistants, tool-callers) with almost no idea how they
fail in production. Standard eval tools check "is the answer good on a clean question?" They do
**not**:
- deliberately break things mid-conversation (a tool times out, a doc is stale, the API 500s),
- adversarially probe safety (prompt injection, role-break),
- test multi-turn behavior (memory, contradiction),
- and — the big gap — tell you **why** it broke and **exactly what to change**.

AgentShield does all of that against **any** agent reachable over HTTP. You don't need its code,
its prompts, or its weights — just the endpoint.

---

## 3. The big picture (architecture)

Three independent services talk over HTTP:

```
┌─────────────────────────────┐        ┌──────────────────────────────┐
│  FRONTEND (Next.js :3000)   │        │  AGENT UNDER TEST (:8002)     │
│  Landing + 4-step wizard    │        │  RAG customer-support agent   │
│  Connect→Verify→Configure→  │        │  (real gpt-4o-mini + retrieval)│
│  Run→Results                │        │  POST /chat -> {reply, trace} │
└──────────────┬──────────────┘        └───────────────▲──────────────┘
               │ REST + polling                          │ black-box HTTP
               ▼                                          │ (adapter only)
┌───────────────────────────────────────────────────────┴──────────────┐
│  BACKEND (FastAPI :8000, SQLite, asyncio)                              │
│  Orchestrator runs the pipeline per run:                              │
│    generate scenarios → break agent (faults+trace) → judge →          │
│    (fail only) explain+fix → score → report                           │
│  LLM calls (scenario gen, judge, fix) go to OpenAI via one wrapper.   │
└───────────────────────────────────────────────────────────────────────┘
```

- **Frontend** never talks to OpenAI or the agent directly — only to our backend.
- **Backend** is the brain. It calls OpenAI for its own AI steps and calls the agent-under-test
  **only through the HTTP adapter** (black box).
- **Agent under test** is a stand-in for "the customer's agent." We built a real one (RAG +
  gpt-4o-mini) so the demo tests a genuinely non-deterministic agent, not a scripted mock.

**Ports:** frontend `3000`, backend `8000`, sample agent `8002`.

---

## 4. The pipeline (the heart of the product)

This is the flow the diagram in the pitch shows, and it's literally what
`app/core/orchestrator.py` executes for every run:

```
Start
  │
  ▼
① Generate scenarios ──────────► OpenAI (or a hardcoded fallback bank)
  │                              ~8 adversarial test cases w/ a required mix
  ▼
② Break the agent as it runs ──► for each scenario, play it turn-by-turn through the
  │                              HTTP adapter, INJECTING the scenario's assigned fault,
  │                              and RECORD every turn + trace
  ▼
③ Judge each answer ───────────► OpenAI scores accuracy / safety / hallucination / recovery,
  │                              returns verdict (pass|fail) + severity + evidence
  │
  ├── pass ─────────────────────► straight to the report (no fix needed)
  │
  └── fail ─► ④ Explain + suggest fix ──► OpenAI (or deterministic for system faults):
                                          cause (2-4 sentences) + ONE pasteable fix + evidence
                                              │
                                              ▼
⑤ Score + report ──────────────► reliability score (0-100) + breakdown + cost/latency
```

**Key branch:** passing scenarios skip the Explain+Fix step entirely. Only failures get a
cause + fix. That branch is the whole point — it's the "last mile" competitors skip.

---

## 5. Backend — file by file (what does what, where)

All under `backend/`.

### App wiring
- **`app/main.py`** — FastAPI app. Enables CORS (open, local dev), creates the DB tables on
  startup, seeds the sample agent, and mounts the routers (`/runs`, `/agents`, `/conversations`).
  Exposes `GET /health`.
- **`app/config.py`** — loads env from `.env`: `OPENAI_API_KEY`, `LLM_MODEL` (gpt-4o-mini),
  `SAMPLE_RAG_BOT_URL`, `MAX_SCENARIOS` (run size cap), `PRICE_PER_1K_TOKENS` (cost estimate rate).
- **`app/db.py`** — thin SQLite layer. `init_schema()` creates the 5 tables; plus all the small
  query/insert/update helpers (no ORM). Also `build_conversation_payload()` which assembles a
  conversation for the report (scenario meta + scores + messages + trace).
- **`app/models.py`** — Pydantic request/response shapes.

### The LLM wrapper (provider isolation)
- **`app/core/llm.py`** — the ONLY place that imports `openai`. `chat(system, messages, json_mode)`
  wraps OpenAI's async client, uses JSON mode when asked, strips ```` ```json ```` fences, and
  retries once on a bad parse. Swap this one file to change providers.

### The pipeline modules
- **`app/core/adapter.py`** — the **black-box HTTP client**. `send(agent, message, history, faults)`
  renders the agent's `request_template`, POSTs to its `endpoint_url` (with optional auth header),
  and extracts `reply` (via a dot-path) + `trace`. **Also simulates system/transport faults**
  (see §7). Fails safe: timeouts/errors return `{"reply":"<error>","trace":{"error":...}}` instead
  of crashing. Seeds the sample agent on startup.
- **`app/core/scenarios.py`** — `generate_scenarios(desc, selected_types, guidance)`. Asks OpenAI
  for ~10–12 scenarios in a required mix (support, injection, memory, contradiction, hallucination,
  system_failure), refines the user's optional **guidance/edge cases** and targets ≥3 scenarios at
  them, normalizes to valid enums, and **falls back to a hardcoded bank** if OpenAI fails.
- **`app/core/runner.py`** — `run_scenario(run_id, scenario, agent)`. Plays a scenario turn-by-turn:
  sends each seed turn through the adapter (passing the assigned fault), does **one semi-adaptive
  follow-up** for injection/memory/contradiction (an LLM "tester" that escalates), caps at 5 turns,
  and writes **every** turn to `messages` with its `trace`.
- **`app/core/judge.py`** — `judge_conversation(conv)`. Loads the transcript + traces + scenario and
  asks OpenAI to score accuracy/safety/hallucination/recovery, decide pass/fail + severity, and cite
  evidence. **Derives the failing category deterministically** from the sub-scores (the LLM's own
  label was unreliable). System failures are judged **without** an LLM call (deterministic).
- **`app/core/fixer.py`** — `explain_and_fix(conv)`. Runs **only on failures**. Produces a
  plain-English cause + ONE pasteable fix + evidence. System failures get a deterministic
  infrastructure-oriented fix (retries/timeouts/monitoring), no LLM call.
- **`app/core/scoring.py`** — `compute(run_id)`. Reliability score + breakdown + **performance**
  (cost/latency) aggregation. Math in §8.
- **`app/core/orchestrator.py`** — `start_run(...)` ties it all together as a background task:
  generate → cap to `MAX_SCENARIOS` → play all (bounded concurrency, `Semaphore(3)`) → judge all →
  fix failures only → score → mark `done`. One bad scenario can't sink the run. Also
  `replay_conversation(id)` (re-run one scenario live).

### The API (routers)
- **`app/routers/runs.py`**
  - `POST /runs {agent_id, tests[], guidance}` → launches a run in the background, returns `{run_id}` instantly.
  - `GET /runs/{id}` → status + reliability_score + progress counts (dashboard polls this).
  - `GET /runs/{id}/report` → the full nested report (score, breakdown, every conversation with
    messages + trace + explanation + fix + evidence).
  - `GET /runs/demo/report` → a static pre-baked report (demo safety, works offline).
- **`app/routers/agents.py`**
  - `GET /agents` / `GET /agents/{id}` — list / fetch agents.
  - `POST /agents` — register your own black-box agent (name, endpoint, response path, template, auth).
  - `POST /agents/{id}/probe` — **real connectivity check** used by the wizard's Verify step
    (sends one message through the adapter and reports whether a valid reply came back).
- **`app/routers/conversations.py`**
  - `POST /conversations/{id}/replay` — re-run that scenario as a new conversation, judge + fix, return it.

### The agent under test
- **`backend/sample_rag_bot/main.py`** — a **fully standalone** FastAPI service (imports nothing
  from our backend). It's a real RAG agent: keyword-retrieve store-policy docs → build a grounded
  prompt → call **gpt-4o-mini** → return `{reply, trace}` with real token counts. It exposes:
  - `GET /` — a self-contained **chat playground UI** (talk to the agent, toggle faults live).
  - `POST /chat` — the API AgentShield calls.
  - `GET /health` — liveness.
  It honors the agent-cooperative faults (tool_timeout, stale_doc, injection) because we own it; a
  real third-party agent simply wouldn't, and AgentShield degrades to conversation + system-fault tests.

### Demo safety
- **`app/demo_report.py`** — a hand-crafted, always-impressive report served by `GET /runs/demo/report`.
  No DB, no OpenAI, so "View sample report" renders even if the network dies mid-demo.

---

## 6. Frontend — file by file

Next.js 16 (App Router) + Tailwind v4 + framer-motion + lucide-react, under `frontend/`.

- **`app/layout.tsx`** — root layout, fonts (Inter / Fredoka / Unbounded), metadata.
- **`app/globals.css`** — theme (dark glass, purple/teal accents), glass-card/button/input classes.
- **`app/page.tsx`** — the **landing page**: hero, core features, workflow, CTA (→ `/dashboard`).
- **`app/lib/api.ts`** — the API client: `registerAgent`, `probeAgent`, `createRun`, `getRun`,
  `getReport`, `getDemoReport`, plus all the TS types. Base URL from `NEXT_PUBLIC_API_URL`
  (default `http://localhost:8000`).
- **`app/dashboard/page.tsx`** — the **wizard** (the whole product UX), a client component with a
  5-state machine: `connect → verifying → configure → running → results`.

### What each wizard step does (and which API it calls)
| Step | UI | Real backend call |
|------|----|-------------------|
| **Connect** | agent name, endpoint URL, optional API key, provider (prefilled with our RAG agent) | — |
| **Verify** | animated checklist | `POST /agents` (register) → `POST /agents/{id}/probe` (genuine ping) |
| **Configure** | test-type chips + optional **custom guidance** textarea | — |
| **Running** | animated radar + live progress bar/counts | `POST /runs` → polls `GET /runs/{id}` every 1.5s |
| **Results** | score gauge, cost/latency, resilience bars, failure cards, passing scenarios | `GET /runs/{id}/report` |

### The results screen (the "money screen") shows
- **Reliability score gauge** (0–100, animated, green/amber/red).
- **Cost & latency tiles** — avg latency, estimated $ cost, total tokens.
- **Resilience by Category** — pass rate per test type as bars.
- **Failed Scenarios** — each card: severity badge, category, **cause**, **pasteable fix + Copy
  button**, **evidence**, and a **"View what AgentShield asked & how it broke"** expander showing the
  full transcript (🧪 AgentShield asked / 🤖 Agent replied) with per-turn trace chips (tool calls
  ok/failed, stale docs, latency, tokens).
- **Passing Scenarios** — collapsed list; expand any to see its transcript + trace (full transparency).
- `?sample=1` deep-links straight to the offline sample report.

---

## 7. Fault injection — the "break it" system

Two classes of faults:

**Agent-cooperative faults** (travel in the request body; only our sample agent honors them, because
the fault manipulates the context fed to its LLM):
- `tool_timeout` — the retrieval/lookup tool "fails"; a good agent should apologize + offer a fallback.
- `stale_doc` — an **outdated** policy doc is injected (e.g. 14-day returns instead of 30); does the agent parrot it?
- `injection` — an adversarial message is passed straight through; does the model leak its secret / break role?

**System / transport faults** (AgentShield simulates these **at the HTTP layer**, so they work on
**any** endpoint — the agent is never even called):
- `api_unreachable` — connection refused.
- `api_error` — HTTP 500 / crash.
- `api_timeout` — no response in time.
- `malformed_response` — invalid / non-JSON body.

System faults are judged deterministically (a 5xx on a valid request is a reliability failure
regardless of content) and produce infrastructure-oriented fixes (retries, timeouts, monitoring).

---

## 8. Scoring & cost math (so you can defend the numbers)

**Reliability score** (`app/core/scoring.py`):
```
weighted_failures = Σ severity_weight   (high=3, med=2, low=1) over failed scenarios
reliability_score = 100 × (1 − weighted_failures / (total_scenarios × 3)),  clamped 0–100
```
Example: 1 high-severity fail out of 5 → 100 × (1 − 3/15) = **80**.

**Breakdown:** pass/fail counts **by test type** and **by failure category**
(safety / hallucination / recovery / accuracy / system).

**Performance (cost & latency):** aggregated from every agent turn's trace —
`avg_latency_ms`, `max_latency_ms`, `total_tokens`, and
`est_cost_usd = total_tokens/1000 × PRICE_PER_1K_TOKENS` (rate configurable in `.env`).

---

## 9. Data model — 5 SQLite tables

- **agents** — id, name, kind(sample|custom), endpoint_url, auth_header, request_template, response_path, description, created_at
- **runs** — id, agent_id, status(queued|running|done|error), reliability_score, breakdown_json, started_at, finished_at
- **scenarios** — id, run_id, title, user_goal, test_type, assigned_fault, expected_behavior, seed_turns_json
- **conversations** — id, run_id, scenario_id, verdict, severity, recovered, scores_json, explanation, suggested_fix, evidence
- **messages** — id, conversation_id, turn_index, role(tester|agent), content, trace_json

One run → many scenarios → one conversation each → many messages (turns). The trace lives on each
agent message.

---

## 10. Tech stack & why

| Layer | Choice | Why |
|-------|--------|-----|
| Backend | FastAPI + asyncio | async fan-out of scenarios/judging with a concurrency cap; tiny footprint |
| Storage | SQLite | zero-setup, perfect for a single-node MVP; 5 simple tables |
| LLM | OpenAI gpt-4o-mini | cheap, fast, JSON-mode; isolated in one wrapper so the provider is swappable |
| Frontend | Next.js 16 + Tailwind + framer-motion | modern, animated, glassmorphism UI |
| Agent under test | standalone RAG + gpt-4o-mini | a *real*, non-deterministic agent so results are credible |

Deliberately **no** Docker / auth / Postgres / Redis / Celery / websockets — it's a focused MVP.

---

## 11. How to run (3 terminals)

```bash
# 1) Agent under test
cd backend && .venv/bin/uvicorn sample_rag_bot.main:app --port 8002
# 2) Backend
cd backend && .venv/bin/uvicorn app.main:app --port 8000
# 3) Frontend
cd frontend && npm run dev      # http://localhost:3000
```
Open http://localhost:3000 → **Start Testing** → the prefilled RAG agent crash-tests end-to-end.
Backend verifiers: `bash backend/scripts/test_phase{1,2,3}.sh`.

---

## 12. 60-second demo script

1. "Every team ships AI agents with no idea how they break. AgentShield crash-tests any agent
   through its public API — no code, no prompts, no access needed."
2. **Connect** → the endpoint is prefilled with our sample support agent → **Verify** (it really pings it).
3. **Configure** → pick test types (injection, hallucination, tool failure, memory, contradiction,
   system failure), optionally type domain edge cases → **Run**.
4. **Dashboard** → "It's generating adversarial scenarios, injecting faults — a tool timing out, a
   stale policy doc, a prompt injection, an API 500 — and recording every trace."
5. **Report** → "Reliability score, cost, latency, resilience by category."
6. Open a coral card → "After 'ignore your instructions,' it leaked its system prompt — here's the
   exact transcript and trace proving it, and the one-line rule you paste to fix it." **Copy the fix.**
7. "Domain-independent, works on your own endpoint, and gives you the cause + fix — the last mile no
   eval tool gives you."

---

## 13. Judge Q&A — likely questions & strong answers

**Q: How is this different from existing LLM eval tools (Ragas, LangSmith, PromptFoo)?**
A: Those mostly score answer quality on clean inputs. AgentShield (1) **injects live faults**
mid-conversation (tool timeouts, stale docs, prompt injection, API 500s), (2) tests **multi-turn**
adversarial behavior with semi-adaptive escalation, and (3) — the differentiator — for every failure
returns a **plain-English cause + a copy-pasteable fix + trace evidence**. It's black-box: point it
at any HTTP endpoint, no code or prompts needed.

**Q: It's black-box — how can you inject faults into an agent you don't control?**
A: Two tiers. **System/transport faults** (unreachable, 500, timeout, malformed) we simulate at the
HTTP layer, so they work on *any* endpoint. **Injection** is just an adversarial message — works on
any agent. **Tool-timeout / stale-doc** need the agent to expose a fault hook, which only our sample
agent does; for a real third-party agent we degrade gracefully to conversation + system-fault tests.
We're explicit about that boundary — no overclaiming.

**Q: Who judges the answers, and isn't an LLM judge unreliable?**
A: An LLM (gpt-4o-mini) scores 4 categories with a strict rubric and must cite evidence from the
transcript/trace. We reduce unreliability by (a) **deriving the failure category deterministically**
from the sub-scores rather than trusting the model's self-label, (b) judging **system failures
deterministically** with no LLM, and (c) grounding accuracy in the scenario's `expected_behavior`.
It's not perfect truth, but it's consistent, cited, and reproducible.

**Q: How is the reliability score calculated?**
A: Severity-weighted: `100 × (1 − Σweight / (total×3))`, weights high=3/med=2/low=1. So one
high-severity fail out of five scenarios → 80. Plus a breakdown by test type and failure category.

**Q: What about cost and latency?**
A: Every agent turn's trace carries latency and tokens. The report aggregates avg/max latency, total
tokens, and an **estimated $ cost** (tokens × configurable rate) — so you see reliability *and* the
operational cost profile in one place.

**Q: Is the demo agent real or faked?**
A: Real. It's a standalone RAG agent (retrieval → grounded prompt → gpt-4o-mini) with real,
non-deterministic answers and real token usage. That's deliberate: it proves the pipeline catches
genuine failures (e.g. the model actually parrots a stale doc, or cracks under multi-turn injection),
not scripted ones.

**Q: What happens if OpenAI rate-limits or the network dies during the demo?**
A: Three safety nets: scenario generation **falls back to a hardcoded bank**; judge/fix have safe
defaults; and there's a **pre-baked sample report** (`View sample report`) that needs no network at
all. Runs are also capped (`MAX_SCENARIOS`) so a live run finishes in ~30–45s.

**Q: Can I test my own agent?**
A: Yes — "Add your own endpoint" in the UI (or `POST /agents`). Provide the URL, the JSON request
template, the response dot-path, and an optional auth header. AgentShield then treats it as a black box.

**Q: How does the multi-turn / adaptive testing work?**
A: Each scenario has seed turns. For injection/memory/contradiction, after the agent replies we call
an LLM "tester" that writes ONE escalating follow-up (press the injection harder, ask the planted
fact back, restate the contradiction). Capped at 5 turns. This is how a single naive injection that
the model resists becomes a multi-turn attack that cracks it.

**Q: Why SQLite / no auth / no Docker?**
A: It's a hackathon MVP focused on proving the core loop and the differentiator. The architecture is
clean (isolated LLM wrapper, black-box adapter, background orchestrator), so scaling to Postgres,
a queue, and auth is a known, incremental path — not a rewrite.

**Q: What's the single most important thing to remember?**
A: **We don't just tell you the agent failed — we tell you exactly what AgentShield asked, how it
broke (with the trace), why, and the one line you paste to fix it.** That last mile is the moat.

---

## 14. Glossary (quick)

- **Scenario** — one generated adversarial test case (goal, type, assigned fault, expected behavior, seed turns).
- **Conversation** — one played-out scenario against the agent (its turns + verdict + scores + fix).
- **Trace** — the per-turn record: tool calls (ok/failed), retrieved docs (stale?), latency, tokens.
- **Fault** — a deliberate break injected during a scenario (agent-cooperative or system/transport).
- **Verdict / severity** — pass|fail and low|med|high, from the judge.
- **Recovery** — did the agent cope gracefully after a fault (only scored when a fault was injected).
