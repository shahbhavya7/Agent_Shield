#!/usr/bin/env bash
# Phase 3 end-to-end verifier: judge + explain/fix + scoring + report/replay/agents API.
# Prereqs (two terminals):
#   1) RAG agent:  .venv/bin/uvicorn sample_rag_bot.main:app --port 8002
#   2) backend:    .venv/bin/uvicorn app.main:app --port 8000
# Then:            bash scripts/test_phase3.sh
set -u
pass=0; fail=0
ok(){ echo "  ✅ $1"; pass=$((pass+1)); }
no(){ echo "  ❌ $1"; fail=$((fail+1)); }

curl -sf localhost:8000/health >/dev/null || { echo "backend down on :8000"; exit 1; }
curl -sf localhost:8002/health >/dev/null || { echo "RAG agent down on :8002"; exit 1; }

echo "== 1. Launch a full run and wait for scoring =="
RID=$(curl -s -X POST localhost:8000/runs -H 'content-type: application/json' \
  -d '{"agent_id":1,"tests":["support","injection","memory","contradiction","hallucination"]}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('run_id',''))")
echo "    run_id=$RID"
score="null"; status=""
for i in $(seq 1 45); do
  resp=$(curl -s localhost:8000/runs/$RID)
  status=$(echo "$resp" | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])")
  { [ "$status" = "done" ] || [ "$status" = "error" ]; } && break
  sleep 3
done
score=$(echo "$resp" | python3 -c "import sys,json;print(json.load(sys.stdin)['reliability_score'])")
[ "$status" = "done" ] && ok "run done" || no "run status=$status"
python3 -c "exit(0 if 0<=float('$score')<=100 else 1)" 2>/dev/null && ok "reliability_score=$score (0-100)" || no "bad score: $score"

echo "== 2. Every conversation is judged (verdict + severity) =="
unjudged=$(sqlite3 agentshield.db "select count(*) from conversations where run_id=$RID and verdict is null;")
[ "$unjudged" = "0" ] && ok "all conversations judged" || no "$unjudged unjudged conversations"

echo "== 3. Recovery flag set (not null) for fault scenarios =="
recov=$(sqlite3 agentshield.db "
  select count(*) from conversations c join scenarios s on c.scenario_id=s.id
  where c.run_id=$RID and s.assigned_fault!='none' and c.recovered is not null;")
[ "$recov" -ge 1 ] && ok "$recov fault-scenarios have a recovered flag" || no "recovered never set for faults"

echo "== 4. BRANCH: failures have explanation+fix; passes do NOT =="
fails_with_fix=$(sqlite3 agentshield.db "select count(*) from conversations where run_id=$RID and verdict='fail' and suggested_fix is not null;")
fails_total=$(sqlite3 agentshield.db "select count(*) from conversations where run_id=$RID and verdict='fail';")
pass_with_fix=$(sqlite3 agentshield.db "select count(*) from conversations where run_id=$RID and verdict='pass' and suggested_fix is not null;")
[ "$fails_with_fix" = "$fails_total" ] && ok "all $fails_total failures have a suggested_fix" || no "some failures missing a fix ($fails_with_fix/$fails_total)"
[ "$pass_with_fix" = "0" ] && ok "passing conversations have NO fix (branch correct)" || no "$pass_with_fix passes wrongly got a fix"

echo "== 5. Report endpoint returns nested payload =="
curl -s localhost:8000/runs/$RID/report | python3 -c "
import sys,json
r=json.load(sys.stdin)
assert r['reliability_score'] is not None
assert r['breakdown'] and 'by_failure_category' in r['breakdown']
assert len(r['conversations'])>=5
c=r['conversations'][0]
assert 'messages' in c and 'scores' in c
print('    score',r['reliability_score'],'| convs',len(r['conversations']),'| cats',r['breakdown']['by_failure_category'])
" && ok "report payload well-formed" || no "report payload malformed"

echo "== 6. Agents endpoint lists the seeded sample =="
curl -s localhost:8000/agents | grep -q '"kind": *"sample"\|"kind":"sample"' && ok "GET /agents lists sample agent" || no "sample agent not listed"

echo "== 7. Replay re-runs a failed scenario as a new conversation =="
FID=$(sqlite3 agentshield.db "select id from conversations where run_id=$RID and verdict='fail' limit 1;")
if [ -n "$FID" ]; then
  NEW=$(curl -s -X POST localhost:8000/conversations/$FID/replay | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))")
  [ -n "$NEW" ] && [ "$NEW" != "$FID" ] && ok "replay of conv $FID -> new conv $NEW" || no "replay did not create a new conversation"
else
  echo "  (no failures to replay — skipped)"
fi

echo
echo "==================  $pass passed, $fail failed  =================="
[ "$fail" = "0" ] && echo "PHASE 3 GREEN ✅  (run_id=$RID)" || echo "See failures above."
exit $fail
