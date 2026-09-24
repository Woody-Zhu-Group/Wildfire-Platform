#!/usr/bin/env bash
# Read-only smoke test for the EC2 backend. Run on the backend host after a deploy:
#
#   bash scripts/smoke_test.sh
#
# Checks each service health endpoint, then asks the agent 6 questions and
# compares the route it chose with the expected route. Nothing is written
# except the agent's own logs. Model-path questions spend OpenRouter credits
# (cents per run).
#
# Environment:
#   SMOKE_HOST          host to test (default 127.0.0.1)
#   SMOKE_ASK_TIMEOUT   seconds per /ask call (default 120)
#   SMOKE_SKIP_ASK      1 to run only the health checks (default 0)

set -u

HOST="${SMOKE_HOST:-127.0.0.1}"
ASK_TIMEOUT="${SMOKE_ASK_TIMEOUT:-120}"
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

report() {
  # report <PASS|FAIL> <name> <detail>
  local status="$1" name="$2" detail="$3"
  if [ "$status" = "PASS" ]; then
    PASSED=$((PASSED + 1))
    printf 'PASS  %s: %s\n' "$name" "$detail"
  else
    FAILED=$((FAILED + 1))
    printf 'FAIL  %s: %s\n' "$name" "$detail"
  fi
}

check_http() {
  # check_http <name> <url>
  local name="$1" url="$2" code
  code="$(curl -s -o "$TMP_DIR/body" -w '%{http_code}' --max-time 15 "$url" || true)"
  [ "$code" = "000" ] && code="no response"
  if [ "$code" = "200" ]; then
    report PASS "$name" "$url returned 200"
  else
    report FAIL "$name" "$url returned ${code:-no response}"
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
  echo "== POST /ask checks =="

  ask() {
    # ask <check id> <name> <question>
    local id="$1" name="$2" question="$3" body code detail
    body="$("$PY" -c 'import json, sys; print(json.dumps({"question": sys.argv[1]}))' "$question")"
    code="$(curl -s -o "$TMP_DIR/ask_$id.json" -w '%{http_code}' --max-time "$ASK_TIMEOUT" \
      -H 'Content-Type: application/json' -d "$body" "http://$HOST:8004/ask" || true)"
    if [ "$code" != "200" ]; then
      [ "$code" = "000" ] && code="no response"
      report FAIL "$name" "POST /ask returned ${code:-no response} for: $question"
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
caveat_ids = sorted({str(item.get("id")) for item in (data.get("qualifications") or []) if isinstance(item, dict)})
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
    wanted = {"city_center_point", "iou_territory_not_provider"}
    missing = sorted(wanted - set(caveat_ids))
    verdict(
        route.get("path") == "deterministic"
        and route.get("rule") == "city_point_context"
        and status == "answer"
        and not missing,
        "expected a deterministic city_point_context answer with the city_center_point "
        "and iou_territory_not_provider caveats"
        + (f"; missing caveats {', '.join(missing)}; got {caveat_ids}" if missing else ""),
    )
elif check == "modesto_count":
    verdict(
        route.get("path") == "clarification" and route.get("rule") == "city_needs_place",
        "expected clarification city_needs_place (a city is not a count filter)",
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
      "PASS "*) report PASS "$name" "${detail#PASS }" ;;
      *) report FAIL "$name" "${detail#FAIL }" ;;
    esac
  }

  ask single "single utility count" "How many PG&E utility-attributed ignitions were there in 2024?"
  ask multi "multi-utility count" "Give me the 2022 ignition count for PG&E, SCE, and SDG&E"
  ask modesto "city territory (Modesto)" "What utility service territory contains Modesto?"
  ask modesto_count "city count clarifies (Modesto)" "How many CAL FIRE incidents were there in Modesto in 2023?"
  ask epss_rank "EPSS utility ranking" "Rank utilities by EPSS events in 2023"
  ask live "live question" "What wildfires are burning right now?"
fi

echo "== Summary =="
echo "passed: $PASSED  failed: $FAILED"
if [ "$FAILED" -gt 0 ]; then
  exit 1
fi
exit 0
