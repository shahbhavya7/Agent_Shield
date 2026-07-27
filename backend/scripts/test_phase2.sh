#!/usr/bin/env bash
# Phase 2 end-to-end verifier: adapter + scenario gen + runner + /runs API.
# Prereqs (two terminals):
#   1) RAG agent:  .venv/bin/uvicorn sample_rag_bot.main:app --port 8002
#   2) backend:    .venv/bin/uvicorn app.main:app --port 8000
# Then:            bash scripts/test_phase2.sh
set -u
pass=0; fail=0
ok(){ echo "  ✅ $1"; pass=$((pass+1)); }
no(){ echo "  ❌ $1"; fail=$((fail+1)); }

echo "== 0. Services reachable =="
curl -sf localhost:8000/health >/dev/null && ok "backend :8000" || { no "backend down"; exit 1; }
curl -sf localhost:8002/health >/dev/null && ok "RAG agent :8002" || { no "RAG agent down"; exit 1; }

echo "== 1. Sample RAG agent is seeded (agents row id=1) =="
sqlite3 agentshield.db "select id,kind from agents;" | grep -q "1|sample" && ok "RAG agent seeded id=1" || no "sample agent not seeded"

echo "== 2. POST /runs returns a run_id immediately =="
RID=$(curl -s -X POST localhost:8000/runs -H 'content-type: application/json' \
  -d '{"agent_id":1,"tests":["support","injection","memory","contradiction"]}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('run_id',''))")
[ -n "$RID" ] && ok "run launched, run_id=$RID" || { no "no run_id returned"; exit 1; }

echo "== 3. Poll GET /runs/{id} until done (fault injection + trace recording happen here) =="
status=""
for i in $(seq 1 30); do
  resp=$(curl -s localhost:8000/runs/$RID)
  status=$(echo "$resp" | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])")
  [ "$status" = "done" ] && break
  [ "$status" = "error" ] && break
  sleep 3
done
echo "    final: $resp"
[ "$status" = "done" ] && ok "run reached status=done" || no "run did not finish (status=$status)"

echo "== 4. Conversations + traces landed in the DB =="
convs=$(sqlite3 agentshield.db "select count(*) from conversations where run_id=$RID;")
traces=$(sqlite3 agentshield.db "select count(*) from messages m join conversations c on m.conversation_id=c.id where c.run_id=$RID and m.role='agent' and m.trace_json is not null;")
[ "$convs" -ge 5 ] && ok "$convs conversations recorded" || no "too few conversations ($convs)"
[ "$traces" -ge 5 ] && ok "$traces agent turns carry a trace_json" || no "traces missing ($traces)"

echo "== 5. Semi-adaptive follow-ups (injection/memory/contradiction get an extra tester turn) =="
adaptive=$(sqlite3 agentshield.db "
  select count(*) from (
    select c.id from conversations c
    join scenarios s on c.scenario_id=s.id
    join messages m on m.conversation_id=c.id
    where c.run_id=$RID and s.test_type in ('injection','memory','contradiction') and m.role='tester'
    group by c.id having count(*) >= 2);")
[ "$adaptive" -ge 1 ] && ok "$adaptive adaptive conversations have multiple tester turns" || no "no adaptive follow-ups found"

echo
echo "==================  $pass passed, $fail failed  =================="
[ "$fail" = "0" ] && echo "PHASE 2 GREEN ✅  (run_id=$RID — inspect it, or use it in Phase 3)" || echo "See failures above."
exit $fail
