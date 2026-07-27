#!/usr/bin/env bash
# AgentShield — start everything with one command.
#   ./run_all.sh
# Starts: backend (8000), 4 sample agents (8002-8005), and the frontend (3000).
# Ctrl-C stops them all.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BE="$ROOT/backend"
FE="$ROOT/frontend"
PY="$BE/.venv/bin/uvicorn"
export PATH="/opt/homebrew/bin:$PATH"   # ensure node/npm are found (Homebrew)

pids=()
cleanup() {
  echo ""
  echo "Stopping AgentShield…"
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null; done
  # free the ports in case anything lingers
  for p in 8000 8002 8003 8004 8005 3000; do lsof -ti:$p | xargs kill 2>/dev/null; done
  exit 0
}
trap cleanup INT TERM

if [ ! -x "$PY" ]; then
  echo "ERROR: backend venv not found at $PY"
  echo "Run:  cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
if [ ! -f "$BE/.env" ]; then
  echo "WARNING: $BE/.env missing — copy .env.example to .env and paste your OPENAI_API_KEY."
fi

start_agent() { # name module port
  echo "  → $1 on :$3"
  ( cd "$BE" && "$PY" "$2" --port "$3" --log-level warning ) &
  pids+=($!)
}

echo "Starting AgentShield services…"
start_agent "Backend (AgentShield API)"      "app.main:app"                 8000
start_agent "Store Support agent"            "sample_rag_bot.main:app"      8002
start_agent "NorthBank (banking) agent"      "sample_agents.banking:app"    8003
start_agent "PeopleDesk (HR) agent"          "sample_agents.hr:app"         8004
start_agent "SafeGuard (insurance) agent"    "sample_agents.insurance:app"  8005

echo "  → Frontend on :3000"
( cd "$FE" && npm run dev ) &
pids+=($!)

sleep 4
cat <<EOF

────────────────────────────────────────────────────────────
AgentShield is up:
  • App (open this):     http://localhost:3000
  • Backend API:         http://localhost:8000/health
  Agents under test (each has its own chat UI at its root):
  • Store Support:       http://localhost:8002/
  • NorthBank (banking): http://localhost:8003/
  • PeopleDesk (HR):     http://localhost:8004/
  • SafeGuard (insurance): http://localhost:8005/

Knowledge docs to upload live in:  docs/agent_kbs/
Press Ctrl-C to stop everything.
────────────────────────────────────────────────────────────
EOF

wait
