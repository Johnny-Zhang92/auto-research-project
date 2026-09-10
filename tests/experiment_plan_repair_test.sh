#!/usr/bin/env bash
set -euo pipefail

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
cp -a "$(cd "$(dirname "$0")/.." && pwd)" "$test_root/research-mode"
cd "$test_root/research-mode"
export PATH="$PWD/tests/bin:$PATH"
export FAKE_PLAN_CASE=canary-malformed

python3 researchctl.py new plan-repair-study --goal "repair malformed experiment plan"
python3 researchctl.py run plan-repair-study

python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/plan-repair-study")
s=json.load(open(p/".research/state.json"))
assert s["phase"] == "plan-approval" and s["status"] == "waiting_approval"
events=(p/".research/events.jsonl").read_text()
assert events.count('"event": "phase_failed"') == 1
assert events.count('"event": "phase_repair_started"') == 1
assert events.count('"event": "phase_repair_completed"') == 1
invalid=list((p/".research/invalid/experiment-plan").glob("*/research/experiment-plan.json"))
assert len(invalid) == 1
failure=json.load(open(invalid[0].parents[1]/"failure.json"))
assert "repetitions must be a positive integer" in failure["reason"]
assert "tasks[0] missing keys: id" in failure["reason"]
before=json.load(open(invalid[0])); after=json.load(open(p/"research/experiment-plan.json"))
assert [task["task_id"] for task in before["tasks"]] == [task["id"] for task in after["tasks"]]
assert before["repetitions"] == {"per_cell": 1, "total_trials": 8}
assert after["repetitions"] == 1
prov=json.load(open(p/".research/provenance/research/experiment-plan.json.provenance.json"))
assert prov["generator"]["agent"] == "research-repairer"
PY

export FAKE_PLAN_REPAIR_FAIL=1
python3 researchctl.py new plan-repair-fail-study --goal "reject invalid experiment plan repair"
if python3 researchctl.py run plan-repair-fail-study; then
  echo "expected invalid experiment plan repair to block" >&2
  exit 1
fi
python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/plan-repair-fail-study")
s=json.load(open(p/".research/state.json"))
assert s["phase"] == "blocked" and "repetitions must be a positive integer" in s["blocker"]
assert len(list((p/".research/invalid/experiment-plan").glob("*/failure.json"))) == 2
assert not (p/".research/provenance/research/experiment-plan.json.provenance.json").exists()
PY

unset FAKE_PLAN_REPAIR_FAIL
export FAKE_REPAIR_BLOCKED=1
python3 researchctl.py new plan-repair-honest-study --goal "allow honest experiment plan repair blocker"
if python3 researchctl.py run plan-repair-honest-study; then
  echo "expected honest repair blocker" >&2
  exit 1
fi
python3 -c 'import json; s=json.load(open("projects/plan-repair-honest-study/.research/state.json")); assert s["phase"] == "blocked" and s["blocker"] == "repair requires scientific judgment"'

unset FAKE_REPAIR_BLOCKED
export FAKE_PLAN_REPAIR_MUTATION=science
python3 researchctl.py new plan-repair-mutation-study --goal "reject scientific mutation"
if python3 researchctl.py run plan-repair-mutation-study; then
  echo "expected scientific mutation to block" >&2
  exit 1
fi
python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/plan-repair-mutation-study")
s=json.load(open(p/".research/state.json"))
assert s["phase"] == "blocked" and "repair changed experiment design" in s["blocker"]
plan=json.load(open(p/"research/experiment-plan.json")); assert plan["tasks"][0]["category"] == "implement"
PY

export FAKE_PLAN_REPAIR_MUTATION=upstream
python3 researchctl.py new plan-repair-upstream-study --goal "reject upstream mutation"
if python3 researchctl.py run plan-repair-upstream-study; then
  echo "expected upstream mutation to block" >&2
  exit 1
fi
python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/plan-repair-upstream-study")
s=json.load(open(p/".research/state.json"))
assert s["phase"] == "blocked" and "phase modified a declared input" in s["blocker"]
h=json.load(open(p/"research/hypothesis-ledger.json")); assert h["hypotheses"][0]["alternative"] == "difference"
PY

export FAKE_PLAN_REPAIR_MUTATION=outside
python3 researchctl.py new plan-repair-outside-study --goal "reject outside write"
if python3 researchctl.py run plan-repair-outside-study; then
  echo "expected outside write to block" >&2
  exit 1
fi
python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/plan-repair-outside-study")
s=json.load(open(p/".research/state.json"))
assert s["phase"] == "blocked" and "outside its allowed paths" in s["blocker"]
assert not (p/"scripts/injected-plan.py").exists()
PY

unset FAKE_PLAN_REPAIR_MUTATION
export FAKE_PLAN_CASE=budget-mismatch
python3 researchctl.py new plan-repair-budget-study --goal "reject irreparable plan budget"
if python3 researchctl.py run plan-repair-budget-study; then
  echo "expected budget mismatch to block" >&2
  exit 1
fi
python3 -c 'import json; s=json.load(open("projects/plan-repair-budget-study/.research/state.json")); assert s["phase"] == "blocked" and "planned trials 8 exceed budget.max_sessions 2" in s["blocker"]'

export FAKE_PLAN_CASE=contract-expansion
export FAKE_CONTRACT_MAX_SESSIONS=8
python3 researchctl.py new plan-repair-contract-budget-study --goal "reject expansion beyond research contract"
if python3 researchctl.py run plan-repair-contract-budget-study; then
  echo "expected research contract budget expansion to block" >&2
  exit 1
fi
python3 -c 'import json; s=json.load(open("projects/plan-repair-contract-budget-study/.research/state.json")); assert s["phase"] == "blocked" and "budget.max_sessions exceeds the research contract" in s["blocker"]'

echo "experiment plan repair test: PASS"
