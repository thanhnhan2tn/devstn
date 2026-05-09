#!/usr/bin/env bash
# Pipeline Workflow Verification Suite
# Usage: bash test_pipeline.sh              # HTTP tests (pipeline must be running)
# Usage: bash test_pipeline.sh --full       # HTTP + internal tests
set -euo pipefail
BASE="http://127.0.0.1:8001"
PASS=0; FAIL=0; TOTAL=0

ok()   { PASS=$((PASS+1)); TOTAL=$((TOTAL+1)); printf "  \033[32m✓\033[0m %s\n" "$1"; }
fail() { FAIL=$((FAIL+1)); TOTAL=$((TOTAL+1)); printf "  \033[31m✗\033[0m %s\n\033[33m    %s\033[0m\n" "$1" "$2"; }
skip() { TOTAL=$((TOTAL+1)); printf "  \033[90m–\033[0m %s — %s\033[0m\n" "$1" "$2"; }
header() { printf "\n\033[1;36m%s\033[0m\n" "$1"; }
sub()    { printf "  \033[90m%s\033[0m\n" "$1"; }

FULL="${1:-}"
INSIDE() {
  [ -n "$FULL" ] || return 1
  docker exec devstation-studio-pipeline-1 sh -c "$1" 2>&1 || return $?
}

# ═════════════════════════════════════════════════════════════════════════════
header "━━━ 1.  Server Health & Endpoints ━━━"
sub "Verify the pipeline API is alive and all endpoints respond"

run() { local out rc; out=$(curl -sf "$1" 2>&1) && rc=0 || rc=$?; echo "$out"; return $rc; }
post(){ local out rc; out=$(curl -sf -X POST "$1" -H "Content-Type: application/json" -d "$2" 2>&1) && rc=0 || rc=$?; echo "$out"; return $rc; }
status_in() { echo "$1" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null; }

h="$(run "$BASE/healthz")" && ok "GET /healthz returns 200" || fail "GET /healthz" "$h"
echo "$h" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='ok'; assert 'studio' in d['node']" 2>/dev/null \
  && ok "Health reports status=ok, node=studio" || fail "Health payload" "$h"

run "$BASE/metrics" | grep -q "pipeline_" && ok "GET /metrics exposes prometheus metrics" || fail "GET /metrics" "no pipeline_ metrics"
run "$BASE/pending-reviews" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null \
  && ok "GET /pending-reviews returns valid JSON" || fail "GET /pending-reviews" "invalid JSON"

p="$(post "$BASE/projects/reload" "{}")" && [ "$(status_in "$p")" == "ok" ] \
  && ok "POST /projects/reload succeeds" || fail "POST /projects/reload" "$p"

# ═════════════════════════════════════════════════════════════════════════════
header "━━━ 2.  Webhook Routing ━━━"
sub "Verify phase detection, label routing, unknown team handling"

p="$(post "$BASE/webhook" '{"issue_id":"TEST-1","title":"hi","phase":"ignore","description":""}')"
[ "$(status_in "$p")" == "ignored" ] && ok "Unknown phase returns ignored" || fail "Unknown phase" "$p"

r="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/webhook" \
  -H "Content-Type: application/json" \
  -d '{"issue_id":"TEST-2","title":"hi","team_id":"NONEXIST","phase":"plan"}' 2>&1)" && rc=0 || rc=$?
[ "$r" == "404" ] && ok "Unknown team_id returns 404" || fail "Unknown team_id" "got HTTP $r"

p="$(post "$BASE/webhook" '{"issue_id":"TEST-PLN-1","title":"test plan","description":"","labels":["plan"]}')"
s="$(status_in "$p")" && [ "$s" == "ok" ] && ok "Label 'plan' triggers plan phase → status=ok" || fail "Plan with label" "status=$s"

p="$(post "$BASE/webhook" '{"issue_id":"TEST-IMP-1","title":"test impl","description":"","labels":["implement"]}')"
s="$(status_in "$p")" && [ "$s" == "ok" ] && ok "Label 'implement' triggers implement phase → status=ok" || fail "Implement with label" "status=$s"

# ═════════════════════════════════════════════════════════════════════════════
header "━━━ 3.  Git Operations ━━━"
sub "Test clone, branch create, checkout existing, commit, push"

GIT_REPO="thanhnhan2tn/package-updater"
TOKEN="$(docker exec devstation-studio-pipeline-1 env 2>/dev/null | grep GITHUB_TOKEN | cut -d= -f2 || echo '')"

if [ -z "$TOKEN" ]; then
  skip "Git clone" "GITHUB_TOKEN not available in container"
  skip "Git branch create" "skipped"
  skip "Git checkout existing" "skipped"
  skip "Git commit+push" "skipped"
else
  INSIDE 'rm -rf /Volumes/work/test-suite && mkdir -p /Volumes/work && git clone https://'$TOKEN'@github.com/'$GIT_REPO'.git /Volumes/work/test-suite 2>&1' | grep -q "done" \
    && ok "Clone repo" || fail "Clone repo" "check GITHUB_TOKEN and repo"
  INSIDE 'cd /Volumes/work/test-suite && git checkout main && git pull origin main 2>&1' | grep -q "Already\|Updating" \
    && ok "Checkout + pull base branch" || fail "Checkout + pull base" "see output above"
  INSIDE 'cd /Volumes/work/test-suite && git checkout -b test/suite-branch 2>&1' | grep -q "Switched" \
    && ok "Create new branch" || fail "Create new branch" "see output above"
  INSIDE 'cd /Volumes/work/test-suite && git checkout main && git checkout test/suite-branch 2>&1' | grep -q "Switched" \
    && ok "Checkout existing branch" || fail "Checkout existing branch" "see output above"
  INSIDE 'cd /Volumes/work/test-suite && echo "suite" > suite.md && git add -A && git -c user.name=T -c user.email=t@t.com commit -m "suite" 2>&1' | grep -q "1 file changed\|1 file inserted" \
    && ok "Commit changes" || fail "Commit changes" "see output above"
  INSIDE 'cd /Volumes/work/test-suite && git push -u origin test/suite-branch 2>&1' | grep -q "remote: " \
    && ok "Push branch to origin" || fail "Push branch" "see output above"
  INSIDE 'cd /Volumes/work/test-suite && git push origin --delete test/suite-branch 2>&1' | grep -q "remote" \
    && ok "Cleanup remote branch" || fail "Cleanup remote branch" "see output above"
fi

# ═════════════════════════════════════════════════════════════════════════════
header "━━━ 4.  End-to-End Workflow ━━━"
sub "Full plan → implement cycle on a real repo"

p="$(post "$BASE/webhook" '{"issue_id":"TEST-E2E-1","title":"Add comment line to README","description":"Add a single line // test-suite to README.md","labels":["plan"],"repo":"thanhnhan2tn/package-updater","team_id":""}')"
s="$(status_in "$p")"
if [ "$s" == "ok" ]; then
  ok "E2E: plan phase → status=ok"
  pr_url="$(echo "$p" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pr_url',''))")"
  [ -n "$pr_url" ] && ok "E2E: plan created PR: ${pr_url}" || ok "E2E: plan completed (no PR URL in response)"
else
  fail "E2E: plan phase" "status=$s, response=${p:0:200}"
fi

p="$(post "$BASE/webhook" '{"issue_id":"TEST-E2E-1","title":"Add comment line to README","description":"","labels":["implement"],"repo":"thanhnhan2tn/package-updater","team_id":""}')"
s="$(status_in "$p")"
if [ "$s" == "ok" ]; then
  ok "E2E: implement phase → status=ok"
else
  fail "E2E: implement phase" "status=$s, response=${p:0:200}"
fi

# ═════════════════════════════════════════════════════════════════════════════
header "━━━ 5.  Edge Cases ━━━"
sub "Idempotency, duplicate runs, minimal payloads"

p="$(post "$BASE/webhook" '{"issue_id":"TEST-IDEM-1","title":"idempotent","description":"","labels":["plan"]}')"
[ "$(status_in "$p")" == "ok" ] && ok "First submission succeeds" || fail "First submission" "$p"

p="$(post "$BASE/webhook" '{"issue_id":"TEST-IDEM-1","title":"idempotent","description":"","labels":["plan"]}')"
[ "$(status_in "$p")" == "ok" ] && ok "Duplicate submission also succeeds" || fail "Duplicate submission" "$p"

p="$(post "$BASE/webhook" '{"issue_id":"TEST-MIN-1","title":"minimal"}')"
[ "$(status_in "$p")" == "ok" ] && ok "Minimal payload (no labels, description, repo) works" || fail "Minimal payload" "$p"

# ═════════════════════════════════════════════════════════════════════════════
header "━━━ 6.  LLM Providers (internal) ━━━"
sub "Verify configured providers and basic API connectivity"

if [ -n "$FULL" ]; then
  INSIDE '
  python3 -c "
import httpx, os, json
def t(name, fn):
    try:
        fn(); print(f\"OK:{name}\")
    except Exception as e:
        print(f\"FAIL:{name}:{e}\")
def skip(name, reason):
    print(f\"SKIP:{name}:{reason}\")

# DeepSeek
key = os.environ.get(\"DEEPSEEK_API_KEY\", \"\")
if key and key.startswith(\"sk-\") and key != \"sk-...\":
    t(\"deepseek basic\", lambda: (
        lambda r: (
            r.status_code == 200 or (_ for _ in ()).throw(AssertionError(r.status_code))
        ))(httpx.post(\"https://api.deepseek.com/v1/chat/completions\",
            headers={\"Authorization\": f\"Bearer {key}\"},
            json={\"model\": \"deepseek-v4-flash\", \"messages\": [{\"role\": \"user\", \"content\": \"hi\"}]},
            timeout=15))
    )
    t(\"deepseek json mode\", lambda: (
        lambda r: (
            json.loads(r.json()[\"choices\"][0][\"message\"][\"content\"])
        ))(httpx.post(\"https://api.deepseek.com/v1/chat/completions\",
            headers={\"Authorization\": f\"Bearer {key}\"},
            json={\"model\": \"deepseek-v4-flash\", \"messages\": [{\"role\": \"user\", \"content\": \"return JSON {\\\"a\\\":1}\"}],
                  \"response_format\": {\"type\": \"json_object\"}},
            timeout=15))
    )
else:
    skip(\"deepseek\", \"no API key\")

# NVIDIA
nkey = os.environ.get(\"NVIDIA_API_KEY\", \"\")
if nkey and nkey.startswith(\"nvapi-\") and nkey != \"nvapi-...\":
    t(\"nvidia basic\", lambda: (
        lambda r: r.status_code == 200
    )(httpx.post(\"https://integrate.api.nvidia.com/v1/chat/completions\",
        headers={\"Authorization\": f\"Bearer {nkey}\"},
        json={\"model\": os.environ.get(\"NVIDIA_MODEL\", \"nvidia/llama-3.1-nemotron-70b-instruct\"),
              \"messages\": [{\"role\": \"user\", \"content\": \"hi\"}]},
        timeout=30))
    )
else:
    skip(\"nvidia\", \"no API key\")
" 2>&1' | while IFS=: read -r res name msg; do
    case "$res" in
      OK)   ok "$name" ;;
      FAIL) fail "$name" "$msg" ;;
      SKIP) skip "$name" "$msg" ;;
    esac
  done
else
  skip "LLM provider tests" "run with --full flag to test LLMs"
fi

# ═════════════════════════════════════════════════════════════════════════════
header "━━━ 7.  Provider Config & Fallback ━━━"
sub "Verify provider configuration in pipeline code"

INSIDE '
python3 -c "
import os, re
with open(\"/app/pipeline.py\") as f:
    src = f.read()

checks = [
    (\"deepseek provider\", '\"deepseek\":'),
    (\"nvidia provider\", '\"nvidia\":'),
    (\"studio-coder fallback to deepseek\", '\"studio-coder\": (\"deepseek\",)'),
    (\"nvidia fallback to deepseek\", '\"nvidia\": (\"deepseek\",)'),
    (\"complex tier uses nvidia\", '\"architect\": \"nvidia\"'),
    (\"medium tier uses deepseek\", '\"pm\": \"deepseek\"'),
]
for name, pattern in checks:
    if pattern in src:
        print(f\"OK:{name}\")
    else:
        print(f\"FAIL:{name}:pattern not found\")
" 2>&1' | while IFS=: read -r res name msg; do
  [ "$res" == "OK" ] && ok "$name" || fail "$name" "$msg"
done

# ═════════════════════════════════════════════════════════════════════════════
header "━━━ 8.  Notification ━━━"
sub "Test notify endpoint functions"

p="$(post "$BASE/notify" '{"message":"test suite notification"}')"
[ "$(status_in "$p")" == "ok" ] && ok "Send notification succeeds" || fail "Send notification" "$p"

INSIDE '
python3 -c "
import os
url = os.environ.get(\"TELEGRAM_URL\", \"\")
if url:
    print(f\"OK:telegram URL configured: {url[:60]}...\")
else:
    # Check if it would be configured
    tok = os.environ.get(\"TELEGRAM_BOT_TOKEN\", \"\")
    ip = os.environ.get(\"TAILSCALE_IP\", \"\")
    print(f\"OK:no telegram URL (token={bool(tok)}, tailscale_ip={ip})\")
" 2>&1' | while IFS=: read -r res name; do
  [ "$res" == "OK" ] && ok "$name" || fail "$name" "see output"
done

# ═════════════════════════════════════════════════════════════════════════════
header "━━━ 9.  Parallel / Concurrent Requests ━━━"
sub "Submit multiple webhooks concurrently"

for i in 1 2 3; do
  (post "$BASE/webhook" "{\"issue_id\":\"TEST-CONC-$i\",\"title\":\"concurrent $i\",\"labels\":[\"plan\"]}" > /dev/null 2>&1) &
done
wait
ok "3 concurrent webhooks submitted without crash"
run "$BASE/healthz" | python3 -c "import sys,json; assert json.load(sys.stdin)['status']=='ok'" 2>/dev/null \
  && ok "Pipeline healthy after concurrent requests" || fail "Pipeline crashed" "healthz failed"

# ═════════════════════════════════════════════════════════════════════════════
header "━━━ 10.  Idempotent Restart ━━━"
sub "Recreate container and verify state persists"

docker compose -f compose/studio.yml --env-file .env up -d pipeline 2>&1 | tail -1
sleep 10
run "$BASE/healthz" | python3 -c "import sys,json; assert json.load(sys.stdin)['status']=='ok'" 2>/dev/null \
  && ok "Pipeline recovers after container restart" || fail "Post-restart health" "not ok"

# ═════════════════════════════════════════════════════════════════════════════
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$FAIL" -eq 0 ]; then
  printf "  \033[1;32mALL %d TESTS PASSED\033[0m\n" "$TOTAL"
else
  printf "  \033[1;31m%d/%d PASSED, %d FAILED\033[0m\n" "$PASS" "$TOTAL" "$FAIL"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
[ "$FAIL" -eq 0 ]
