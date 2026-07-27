# 🛡️ AgentShield

**Crash-test any AI agent before deployment.** AgentShield generates adversarial scenarios,
injects faults mid-conversation, judges every answer, and — the differentiator — for each
failure produces a plain-English cause + a copy-pasteable fix backed by trace evidence.

```
Start → Generate scenarios → Break agent as it runs → Judge each answer
                                                            │
                                        (pass) ──────► Reliability report
                                        (fail) ──► Explain + suggest fix ──► report
```

- **Backend:** FastAPI + SQLite + asyncio · OpenAI (isolated wrapper) · black-box HTTP adapter.
- **Frontend:** React + Vite + TypeScript · glassmorphism / cyan UI.
- **Agent under test:** a standalone RAG customer-support agent (gpt-4o-mini) reached only
  over HTTP — a stand-in for any agent you'd point AgentShield at.

---

## Prerequisites
- Python 3.11+ and Node 18+.
- An OpenAI API key.

## One-time setup
```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then paste your key into OPENAI_API_KEY=
cd ../frontend && npm install
```

## Run it (three terminals)
```bash
# 1) The agent under test — standalone RAG support agent
cd backend
.venv/bin/uvicorn sample_rag_bot.main:app --port 8002

# 2) AgentShield backend
cd backend
.venv/bin/uvicorn app.main:app --port 8000

# 3) Frontend
cd frontend
npm run dev            # open http://localhost:5173
```

Then open **http://localhost:5173**, pick the agent, and click **Crash-test this agent**.

## Sample agents (4 domains, each standalone with its own KB + chat UI)
Each is a real RAG agent (gpt-4o-mini + retrieval over its own knowledge base), speaks the same
`POST /chat` contract, honors the fault flags, and serves its own chat playground at `GET /`.
Point AgentShield at any of them (enter its `/chat` URL in the wizard).

| Agent | Domain | Port | Run |
|-------|--------|------|-----|
| Store Support (RAG) | e-commerce returns/refunds/shipping | 8002 | `uvicorn sample_rag_bot.main:app --port 8002` |
| NorthBank Support | retail banking (transfers, fees, fraud, loans) | 8003 | `uvicorn sample_agents.banking:app --port 8003` |
| PeopleDesk HR | HR / benefits (PTO, 401k, leave, payroll) | 8004 | `uvicorn sample_agents.hr:app --port 8004` |
| SafeGuard Claims | auto/home insurance claims | 8005 | `uvicorn sample_agents.insurance:app --port 8005` |

New agents share a self-contained factory (`backend/sample_agents/rag_core.py`); each domain file
just supplies its KB docs (current + stale variants), a confidential system-prompt secret (for
injection tests), and sample questions. Scenario generation is **domain-driven** — register an agent
with a banking/HR/insurance description and AgentShield generates domain-appropriate scenarios.

## Try the support agent directly (its own chat UI)
The RAG support agent ships with its own self-contained chat playground:

- **UI:** open **http://localhost:8002/** — chat with the agent, and toggle **⚡ tool_timeout /
  stale_doc / injection** to watch how it behaves under each fault (the trace shows tool
  calls, stale docs, latency, tokens live).
- **Endpoint (black-box API AgentShield uses):**
  ```bash
  curl -s http://localhost:8002/chat -H 'content-type: application/json' \
    -d '{"message":"how long do I have to return an item?","history":[],"faults":[]}'
  # -> { "reply": "...", "trace": { tool_calls, retrieved_docs, latency_ms, tokens } }
  ```
  Request: `{ message: str, history: [{role,content}], faults: ["tool_timeout"|"stale_doc"|"injection"] }`
  · `GET /health` for a liveness check.

## Verify each phase (optional)
```bash
cd backend
bash scripts/test_phase1.sh   # foundation + RAG agent
bash scripts/test_phase2.sh   # adapter + scenario gen + runner
bash scripts/test_phase3.sh   # judge + explain/fix + scoring + report/replay/agents
```

---

## 60-second demo script
1. "Every team ships AI agents with no idea how they break. AgentShield crash-tests any
   agent through its public API — no code, no prompts, no access needed."
2. Pick the **Store Customer-Support Agent (RAG)** → **Crash-test this agent**.
3. Dashboard: "It's generating adversarial scenarios, injecting faults — a tool timing out,
   a stale policy doc, a prompt-injection attempt — and recording every trace."
4. Report: "Reliability score. Here's the breakdown by failure type."
5. Open a coral card: "After 'ignore your instructions,' it leaked its system prompt. Here's
   the exact trace proving it — and the one-line rule you paste to fix it." Copy the fix.
6. Hit **Replay** on a card: "Re-run any single failure live to verify a fix."
7. "Domain-independent, and the same flow accepts your own agent's endpoint. That's the last
   mile no eval tool gives you."

## Demo safety
- **View sample report** (on the home screen) shows a pre-baked report with no backend/OpenAI
  needed — insurance against a flaky network mid-demo. Deep link: `http://localhost:5173/#sample`.
- Every OpenAI call has a fallback (scenario bank, safe judge/fix defaults); one failed
  scenario never fails the whole run.
- Runs are capped (`MAX_SCENARIOS`, default 8) so a live run finishes in ~30–45s.

## How AgentShield reaches the agent
Black-box HTTP only. The agent is a DB row (`endpoint_url`, `request_template`, `response_path`);
the adapter renders the template, POSTs, and extracts `reply` + `trace`. Register your own from
the UI (**+ Add your own endpoint**) or via `POST /agents`. See `docs/PHASES.md` for the full
architecture and per-phase detail.

## Feature notes (post-MVP additions)
- **Custom guidance & edge cases** — optional box on the run screen. Your domain notes are
  refined by the generator and at least 3 scenarios are targeted at them.
- **System-failure testing** — a fault class AgentShield simulates at the HTTP layer, so it
  works on any endpoint: `api_unreachable`, `api_error` (5xx), `api_timeout`,
  `malformed_response`. Judged deterministically → a **system** failure category with
  infrastructure-oriented fixes (retries, timeouts, monitoring).
- **Cost & latency** — the report's performance panel aggregates avg/max latency, total tokens,
  and estimated $ cost across the agent's turns. Tune the rate with `PRICE_PER_1K_TOKENS` in `.env`.
- **Bring your own endpoint** — register any HTTP agent (name, URL, response dot-path, request
  template, optional auth header). Custom agents get conversation + system-failure tests;
  agent-cooperative faults (tool_timeout/stale_doc) only apply to agents that honor a `faults` field.
