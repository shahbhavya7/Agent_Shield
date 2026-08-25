# 🛡️ AgentShield

**Crash-test any AI agent before deployment.** AgentShield generates adversarial test cases,
plays them against your chatbot (injecting simulated failures along the way), judges every
answer, and — for every failure — produces a plain-English explanation and a copy-pasteable
one-line fix, backed by the exact evidence from the conversation.

```
Connect an agent → Generate test cases → Review/edit them → Run the test
                                                                  │
                                              (pass) ──────► Reliability report
                                              (fail) ──► Explain + suggest fix ──► report
```

Want the full plain-English breakdown of every feature? See
**[`docs/APPLICATION_GUIDE.md`](docs/APPLICATION_GUIDE.md)**. Want flowcharts of every screen
and process? See **[`docs/APPLICATION_FLOWS.md`](docs/APPLICATION_FLOWS.md)**.

- **Backend:** FastAPI + PostgreSQL + asyncio · OpenAI (isolated wrapper) · black-box HTTP adapter.
- **Frontend:** Next.js + React + TypeScript · dark glassmorphism UI.
- **Agents under test:** four standalone, real LLM-backed sample chatbots (e-commerce,
  banking, HR, insurance) reached only over HTTP — plus support for connecting your own.

---

## Prerequisites

- Python 3.11+
- Node 18+
- An OpenAI API key
- **PostgreSQL** — either a local install, or **Docker** (recommended, see below)

## One-time setup

### 1. Get a database running

AgentShield stores everything in PostgreSQL. The easiest way to get one running locally is
Docker, so it doesn't collide with any other Postgres you might already have on your machine:

```bash
cd backend
docker compose -f docker-compose.local.yml up -d
```

This starts a dedicated Postgres container on **port 5433** (not the standard 5432, to avoid
clashing with a system-wide Postgres install), with the database, user, and password all set
to `agentshield`. It keeps its data in a Docker volume, so it survives restarts.

> Don't want Docker? You can instead install Postgres yourself and run
> `createdb agentshield`, then point `DATABASE_URL` in `.env` (below) at wherever it lives.

### 2. Backend

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Open `backend/.env` and fill in:
- `OPENAI_API_KEY` — your real key (required — every AI-backed feature needs this)
- `DATABASE_URL` — if you used the Docker step above, set it to:
  ```
  DATABASE_URL=postgresql://agentshield:agentshield@localhost:5433/agentshield
  ```
  (if you installed Postgres yourself instead, use whatever connection string points at your
  own `agentshield` database)

Everything else in `.env.example` has a sensible default and can be left as-is.

### 3. Frontend

```bash
cd frontend
npm install
```

## Run it

**Easiest — one command from the repo root:**

```bash
./run_all.sh
```

This starts, in order: the local Postgres container (if `backend/docker-compose.local.yml`
exists and isn't already running), the AgentShield backend, all 4 sample chatbots, and the
frontend. Press `Ctrl-C` to stop everything (the database container is left running, since
it's cheap to keep around — stop it yourself with
`docker compose -f backend/docker-compose.local.yml down` if you want).

Once it's up, open **http://localhost:3000**.

**Or, manually in separate terminals**, if you want to run pieces individually:

```bash
# Database (if not already running)
cd backend && docker compose -f docker-compose.local.yml up -d

# The 4 sample chatbots (each is its own tiny standalone service)
cd backend
.venv/bin/uvicorn sample_rag_bot.main:app --port 8002    # Store Support
.venv/bin/uvicorn sample_agents.banking:app --port 8003  # NorthBank (banking)
.venv/bin/uvicorn sample_agents.hr:app --port 8004       # PeopleDesk (HR)
.venv/bin/uvicorn sample_agents.insurance:app --port 8005 # SafeGuard (insurance)

# AgentShield backend
cd backend
.venv/bin/uvicorn app.main:app --port 8000

# Frontend
cd frontend
npm run dev            # open http://localhost:3000
```

Then open **http://localhost:3000**, click **Start Testing**, and either connect your own
chatbot or pick one of the 4 sample chatbots to try the whole flow immediately.

## Sample agents (4 domains, each standalone with its own knowledge base + chat UI)

Each is a real chatbot (gpt-4o-mini + retrieval over its own knowledge base) that speaks the
same `/chat` contract AgentShield expects, honors the simulated-fault flags, and serves its
own little chat playground page so you can talk to it directly.

| Agent | Domain | Port | Personality |
|-------|--------|------|-------------|
| Store Support (RAG) | e-commerce returns/refunds/shipping | 8002 | Balanced |
| NorthBank Support | retail banking (transfers, fees, fraud, loans) | 8003 | Balanced |
| PeopleDesk HR | HR / benefits (PTO, 401k, leave, payroll) | 8004 | Deliberately weak/vulnerable (guaranteed demo failure) |
| SafeGuard Claims | auto/home insurance claims | 8005 | Deliberately hardened (guaranteed strong report) |

All four are automatically registered and organized under 3 sample "customers" the first time
the backend starts (see `backend/inventory.yaml` / `backend/mapping.yaml`) — find them under
**Test Existing Agent** on the homepage. You can also point AgentShield at any of their
`/chat` URLs manually via **Connect a New Agent**.

New sample agents share one reusable engine (`backend/sample_agents/rag_core.py`); each domain
file just supplies its own knowledge base, a confidential system-prompt secret (for injection
tests), and sample questions.

## Try a sample agent directly (its own chat UI)

Every sample agent ships its own self-contained chat playground:

- **UI:** open **http://localhost:8002/** (or 8003/8004/8005) — chat with it directly, and
  toggle **⚡ tool_timeout / stale_doc / injection** to watch how it behaves under each
  simulated fault (the trace panel shows tool calls, stale docs, latency, and tokens live).
- **Endpoint** (the black-box API AgentShield actually uses):
  ```bash
  curl -s http://localhost:8002/chat -H 'content-type: application/json' \
    -d '{"message":"how long do I have to return an item?","history":[],"faults":[]}'
  # -> { "reply": "...", "trace": { tool_calls, retrieved_docs, latency_ms, tokens } }
  ```
  Request shape: `{ message: str, history: [{role,content}], faults: ["tool_timeout"|"stale_doc"|"injection"] }`
  · `GET /health` on any of them for a liveness check.

## Demo safety

- **"View a sample report"** (linked from the Connect screen) shows a pre-baked report with
  no backend/database/OpenAI dependency at all — a safety net against a flaky connection
  mid-demo.
- Every AI call has a fallback: a hardcoded backup set of test cases if generation fails, a
  safe default verdict if judging fails, a generic (but still useful) explanation if the
  fix-writer fails. One broken AI call never fails an entire test run.
- Runs are capped (`MAX_SCENARIOS` in `.env`, default 10) so a live run finishes quickly.

## How AgentShield reaches an agent

Black-box HTTP only — AgentShield never reads or needs a chatbot's source code. The chatbot
is stored as a database row (`endpoint_url`, `request_template`, `response_path`); AgentShield
renders the template, sends the request, and extracts the reply + trace from the response.
Register your own from the UI (**Connect a New Agent**) or via `POST /agents`.

## Key features beyond the basic pass/fail loop

- **Customers & saved test-case libraries** — every chatbot is tested "on behalf of" a
  customer; each (customer, chatbot) pair gets its own permanent, editable, reusable set of
  test cases, distinct from any other customer testing the same physical chatbot.
- **Review before you run** — generated test cases are never run blind. You can edit, delete,
  hand-write, or AI-draft-then-tweak any test case, and save the suite without running it.
- **Parallel multi-agent runs** — select several chatbots (or "Run All") on the Existing Agent
  screen and test them all at once, with live per-agent progress and independent, fully
  separate reports for each.
- **Agent knowledge upload** — attach a policy document to a chatbot once; every future test
  generation for it is grounded in that document automatically.
- **Auto-discovery** — if you give AgentShield no info about a chatbot, it probes the chatbot
  itself with a few neutral questions and infers its domain before generating tests.
- **System-failure testing** — 4 simulated backend-outage faults (`api_unreachable`,
  `api_error`, `api_timeout`, `malformed_response`) that work against *any* endpoint, since
  AgentShield fakes them before ever contacting the real chatbot.
- **Cost & latency tracking** — every report aggregates average/max response time, total
  tokens, and an estimated dollar cost across the whole run. Tune the rate with
  `PRICE_PER_1K_TOKENS` in `.env`.

For the full plain-English explanation of every one of these, see
**[`docs/APPLICATION_GUIDE.md`](docs/APPLICATION_GUIDE.md)**. For step-by-step flowcharts of
exactly what happens on each screen and in the backend, see
**[`docs/APPLICATION_FLOWS.md`](docs/APPLICATION_FLOWS.md)**.
