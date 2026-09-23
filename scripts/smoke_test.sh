#!/usr/bin/env bash
# Read-only smoke test for the EC2 backend. Run on the backend host after a deploy:
#
#   bash scripts/smoke_test.sh
#
# Checks each service health endpoint, then asks the agent 5 questions and
# compares the route it chose with the expected route. Nothing is written
# except the agent's own logs.
#
# Checks tagged "requires PR #22" depend on router fixes that are not on main
# until PR #22 (router-paraphrase-fixes) merges. Before that merge they are
# expected to fail and do not count toward the exit code. A service that does
# not respond always counts. After the merge, run with SMOKE_PR22_MERGED=1 so
# they count.
#
# Environment:
#   SMOKE_HOST          host to test (default 127.0.0.1)
#   SMOKE_ASK_TIMEOUT   seconds per /ask call (default 900; model answers can take minutes)
#   SMOKE_PR22_MERGED   1 once PR #22 is on the deployed commit (default 0)
#   SMOKE_SKIP_ASK      1 to run only the health checks (default 0)

set -u

HOST="${SMOKE_HOST:-127.0.0.1}"
ASK_TIMEOUT="${SMOKE_ASK_TIMEOUT:-900}"
PR22_MERGED="${SMOKE_PR22_MERGED:-0}"
SKIP_ASK="${SMOKE_SKIP_ASK:-0}"

PY=""
for candidate in python3 python; do
  if "$candidate" -c 'import json' >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "python3 is required to read JSON responses" >&2
  exit 2
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PASSED=0
FAILED=0
PR22_PENDING=0

report() {
  # report <PASS|FAIL> <name> <requires_pr22 0|1> <detail>
  local status="$1" name="$2" pr22="$3" detail="$4"
  local tag=""
  if [ "$pr22" = "1" ]; then
    tag=" [requires PR #22]"
  fi
  if [ "$status" = "PASS" ]; then
    PASSED=$((PASSED + 1))
    printf 'PASS  %s%s: %s\n' "$name" "$tag" "$detail"
  elif [ "$pr22" = "1" ] && [ "$PR22_MERGED" != "1" ]; then
    PR22_PENDING=$((PR22_PENDING + 1))
    printf 'FAIL  %s%s: %s (expected before PR #22 merges; not a regression)\n' "$name" "$tag" "$detail"
  else
    FAILED=$((FAILED + 1))
    printf 'FAIL  %s%s: %s\n' "$name" "$tag" "$detail"
  fi
}

check_http() {
  # check_http <name> <url>
  local name="$1" url="$2" code
  code="$(curl -s -o "$TMP_DIR/body" -w '%{http_code}' --max-time 15 "$url" || true)"
  [ "$code" = "000" ] && code="no response"
  if [ "$code" = "200" ]; then
    report PASS "$name" 0 "$url returned 200"
  else
    report FAIL "$name" 0 "$url returned ${code:-no response}"
  fi
}

echo "== Health checks on $HOST =="
check_http "data_query health" "http://$HOST:8000/health"
check_http "risk_forecasting health" "http://$HOST:8001/health"
check_http "visualization health" "http://$HOST:8002/health"
check_http "comparison health" "http://$HOST:8003/health"
check_http "agent health" "http://$HOST:8004/health"
check_http "frontend root" "http://$HOST:8765/"

if [ "$SKIP_ASK" = "1" ]; then
  echo "== Skipping /ask checks (SMOKE_SKIP_ASK=1) =="
else
  echo "== POST /ask checks (each can take several minutes on the CPU model host) =="

  ask() {
    # ask <check id> <name> <requires_pr22 0|1> <question>
    local id="$1" name="$2" pr22="$3" question="$4" body code detail
    body="$("$PY" -c 'import json, sys; print(json.dumps({"question": sys.argv[1]}))' "$question")"
    code="$(curl -s -o "$TMP_DIR/ask_$id.json" -w '%{http_code}' --max-time "$ASK_TIMEOUT" \
      -H 'Content-Type: application/json' -d "$body" "http://$HOST:8004/ask" || true)"
    if [ "$code" != "200" ]; then
      # A dead or erroring agent is a real failure even for PR #22 checks.
      [ "$code" = "000" ] && code="no response"
      report FAIL "$name" 0 "POST /ask returned ${code:-no response} for: $question"
      return
    fi
    detail="$("$PY" - "$id" "$TMP_DIR/ask_$id.json" <<'PYEOF'
import json
import re
import sys

check, path = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
route = data.get("route") or {}
status = data.get("status")
answer = data.get("answer_text") or ""
seen = f"status={status} path={route.get('path')} rule={route.get('rule')}"


def verdict(ok, why):
    print(("PASS " if ok else "FAIL ") + why + f" ({seen})")


if check == "single":
    verdict(
        route.get("path") == "deterministic" and route.get("rule") == "filtered_records" and status == "answer",
        "expected a deterministic filtered_records answer",
    )
elif check == "multi":
    names = {
        "PG&E": r"PG&E|Pacific Gas",
        "SCE": r"\bSCE\b|Southern California Edison",
        "SDG&E": r"SDG&E|San Diego Gas",
    }
    missing = [name for name, pattern in names.items() if not re.search(pattern, answer)]
    if status == "clarification":
        verdict(True, "asked a clarifying question instead of answering one utility")
    else:
        verdict(
            status == "answer" and not missing,
            "expected all three utilities in the answer"
            + (f"; missing {', '.join(missing)}" if missing else ""),
        )
elif check == "modesto":
    verdict(
        route.get("path") == "clarification" and route.get("rule") == "city_needs_place",
        "expected clarification city_needs_place",
    )
elif check == "epss_rank":
    verdict(
        route.get("path") == "unsupported" and route.get("rule") == "unsupported_rank_epss_utility",
        "expected unsupported unsupported_rank_epss_utility",
    )
elif check == "live":
    verdict(
        route.get("path") == "unsupported" and route.get("rule") == "unsupported_live_web",
        "expected unsupported unsupported_live_web",
    )
else:
    print(f"FAIL unknown check {check}")
PYEOF
)"
    case "$detail" in
      "PASS "*) report PASS "$name" "$pr22" "${detail#PASS }" ;;
      *) report FAIL "$name" "$pr22" "${detail#FAIL }" ;;
    esac
  }

  ask single "single utility count" 0 "How many PG&E utility-attributed ignitions were there in 2024?"
  ask multi "multi-utility count" 1 "Give me the 2022 ignition count for PG&E, SCE, and SDG&E"
  ask modesto "city territory (Modesto)" 1 "What utility service territory contains Modesto?"
  ask epss_rank "EPSS utility ranking" 1 "Rank utilities by EPSS events in 2023"
  ask live "live question" 1 "What wildfires are burning right now?"
fi

echo "== Summary =="
echo "passed: $PASSED  failed: $FAILED  failed but waiting on PR #22: $PR22_PENDING"
if [ "$FAILED" -gt 0 ]; then
  exit 1
fi
exit 0
