#!/usr/bin/env bash
set -euo pipefail

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
cp -a "$(cd "$(dirname "$0")/.." && pwd)" "$test_root/research-mode"
cd "$test_root/research-mode"
export PATH="$PWD/tests/bin:$PATH"
export FAKE_EVIDENCE_CASE=canary-malformed

python3 researchctl.py new repair-study --goal "repair malformed evidence contract"
python3 researchctl.py run repair-study

python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/repair-study")
s=json.load(open(p/".research/state.json"))
assert s["phase"] == "plan-approval" and s["status"] == "waiting_approval"
events=(p/".research/events.jsonl").read_text()
assert events.count('"event": "phase_failed"') == 1
assert events.count('"event": "phase_repair_started"') == 1
assert events.count('"event": "phase_repair_completed"') == 1
invalid=list((p/".research/invalid/evidence").glob("*/research/evidence-ledger.json"))
assert len(invalid) == 1
before=json.load(open(invalid[0])); after=json.load(open(p/"research/evidence-ledger.json"))
assert before["claims"][0]["statement"] == after["claims"][0]["text"]
assert [q["id"] for q in before["queries"]] == [q["id"] for q in after["queries"]]
assert [e["source"] for e in before["evidence"]] == [e["source"] for e in after["evidence"]]
assert after["queries"][0]["status"] == "completed"
assert after["queries"][1]["status"] == "failed"
repair=json.load(open(p/after["queries"][1]["snapshot"]["path"]))
assert repair["repair_record"] is True and repair["original_response_available"] is False and repair["results"] == []
prov=json.load(open(p/".research/provenance/research/evidence-ledger.json.provenance.json"))
assert prov["generator"]["agent"] == "research-repairer"
PY

export FAKE_REPAIR_FAIL=1
python3 researchctl.py new repair-fail-study --goal "reject an invalid repair"
if python3 researchctl.py run repair-fail-study; then
  echo "expected invalid repair to block" >&2
  exit 1
fi
python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/repair-fail-study")
s=json.load(open(p/".research/state.json"))
assert s["phase"] == "blocked" and "status='success'" in s["blocker"]
assert len(list((p/".research/invalid/evidence").glob("*/failure.json"))) == 2
assert not (p/".research/provenance/research/evidence-ledger.json.provenance.json").exists()
PY

unset FAKE_REPAIR_FAIL
export FAKE_REPAIR_BLOCKED=1
python3 researchctl.py new repair-honest-block-study --goal "allow repair agent to stop honestly"
if python3 researchctl.py run repair-honest-block-study; then
  echo "expected honest repair blocker to stop the project" >&2
  exit 1
fi
python3 -c 'import json; s=json.load(open("projects/repair-honest-block-study/.research/state.json")); assert s["phase"] == "blocked" and s["blocker"] == "repair requires scientific judgment"'

unset FAKE_REPAIR_BLOCKED
export FAKE_REPAIR_MUTATION=source
python3 researchctl.py new repair-mutation-study --goal "reject scientific changes during repair"
if python3 researchctl.py run repair-mutation-study; then
  echo "expected scientific mutation during repair to block" >&2
  exit 1
fi
python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/repair-mutation-study")
s=json.load(open(p/".research/state.json")); assert s["phase"] == "blocked" and "repair changed scientific content" in s["blocker"]
ledger=json.load(open(p/"research/evidence-ledger.json")); assert ledger["evidence"][0]["source"]["title"] == "Offline evidence"
PY

export FAKE_REPAIR_MUTATION=snapshot
python3 researchctl.py new repair-snapshot-study --goal "reject raw snapshot changes during repair"
if python3 researchctl.py run repair-snapshot-study; then
  echo "expected snapshot mutation during repair to block" >&2
  exit 1
fi
python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/repair-snapshot-study")
s=json.load(open(p/".research/state.json")); assert s["phase"] == "blocked" and ("existing snapshot binding" in s["blocker"] or "modified or removed existing snapshot" in s["blocker"])
snapshot=json.load(open(p/"research/evidence/snapshots/Q1.json")); assert snapshot["results"][0]["id"] == "local:test"
PY

export FAKE_REPAIR_MUTATION=outside
python3 researchctl.py new repair-outside-study --goal "reject out-of-scope repair writes"
if python3 researchctl.py run repair-outside-study; then
  echo "expected out-of-scope repair write to block" >&2
  exit 1
fi
python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/repair-outside-study")
s=json.load(open(p/".research/state.json")); assert s["phase"] == "blocked" and "outside its allowed paths" in s["blocker"]
assert not (p/"scripts/injected.py").exists()
PY

echo "evidence repair test: PASS"
