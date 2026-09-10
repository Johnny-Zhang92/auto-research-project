#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp -a "$root" "$tmp/research-mode"
cd "$tmp/research-mode"
export PATH="$PWD/tests/bin:$PATH"

FAKE_HYPOTHESIS_CASE=claim-reference python3 researchctl.py new hypothesis-reference-study --goal "repair claim references"
FAKE_HYPOTHESIS_CASE=claim-reference python3 researchctl.py run hypothesis-reference-study
python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/hypothesis-reference-study")
s=json.load(open(p/".research/state.json")); assert s["phase"] == "plan-approval"
h=json.load(open(p/"research/hypothesis-ledger.json")); assert h["hypotheses"][0]["evidence_ids"] == ["E1"]
events=(p/".research/events.jsonl").read_text()
assert events.count('"event": "phase_repair_started"') == 1
assert events.count('"event": "phase_repair_completed"') == 1
assert '"event": "phase_revision_started"' not in events
prov=json.load(open(p/".research/provenance/research/hypothesis-ledger.json.provenance.json"))
assert prov["generator"]["agent"] == "research-repairer"
PY

FAKE_HYPOTHESIS_CASE=mixed python3 researchctl.py new hypothesis-revision-study --goal "revise mixed claim references and unsupported hypotheses"
FAKE_HYPOTHESIS_CASE=mixed python3 researchctl.py run hypothesis-revision-study
python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/hypothesis-revision-study")
s=json.load(open(p/".research/state.json")); assert s["phase"] == "plan-approval"
h=json.load(open(p/"research/hypothesis-ledger.json")); assert [x["id"] for x in h["hypotheses"]] == ["H1"]
records=list((p/".research/revisions/hypotheses").glob("*.json")); assert len(records) == 1
r=json.load(open(records[0])); assert [(x["hypothesis_id"],x["action"]) for x in r["changes"]] == [("H1","revised"),("H2","removed")]
rp=p/".research/provenance"/(str(records[0].relative_to(p))+".provenance.json")
rprov=json.load(open(rp)); assert rprov["artifact"] == str(records[0].relative_to(p)) and rprov["skills"] == []
failures=list((p/".research/invalid/hypotheses").glob("*/failure.json")); assert len(failures) == 1
reason=json.load(open(failures[0]))["reason"]
assert "contains claim IDs" in reason and "must contain at least 1 item" in reason
events=(p/".research/events.jsonl").read_text()
assert events.count('"event": "phase_revision_started"') == 1
assert events.count('"event": "phase_revision_completed"') == 1
prov=json.load(open(p/".research/provenance/research/hypothesis-ledger.json.provenance.json"))
assert prov["generator"]["agent"] == "research-reviser"
assert not (p/".research/provenance/.research/phase-result.json.provenance.json").exists()
PY
python3 researchctl.py audit hypothesis-revision-study

for mutation in invent-evidence upstream outside bad-diff; do
  project="hypothesis-revision-${mutation}"
  FAKE_HYPOTHESIS_CASE=unsupported python3 researchctl.py new "$project" --goal "reject unsafe revision"
  if FAKE_HYPOTHESIS_CASE=unsupported FAKE_HYPOTHESIS_REVISION_MUTATION="$mutation" python3 researchctl.py run "$project"; then
    echo "expected $mutation hypothesis revision to block" >&2
    exit 1
  fi
  PROJECT="$project" python3 - <<'PY'
import json,os
from pathlib import Path
p=Path("projects")/os.environ["PROJECT"]
s=json.load(open(p/".research/state.json")); assert s["phase"] == "blocked"
events=[json.loads(x) for x in (p/".research/events.jsonl").read_text().splitlines()]
assert any(e.get("failure_class") in {"boundary_violation", "semantic_validation"} for e in events if e["event"] == "phase_failed")
e=json.load(open(p/"research/evidence-ledger.json")); assert e["claims"][0]["text"] == "offline evidence exists"
assert not (p/"scripts/injected-revision.py").exists()
PY
done

FAKE_HYPOTHESIS_CASE=unsupported python3 researchctl.py new hypothesis-revision-honest --goal "allow honest revision blocker"
if FAKE_HYPOTHESIS_CASE=unsupported FAKE_REVISION_BLOCKED=1 python3 researchctl.py run hypothesis-revision-honest; then
  echo "expected honest grounded revision blocker" >&2
  exit 1
fi
python3 -c 'import json; s=json.load(open("projects/hypothesis-revision-honest/.research/state.json")); assert s["phase"] == "blocked" and "cannot retain" in s["blocker"]'

FAKE_HYPOTHESIS_CASE=claim-reference python3 researchctl.py new hypothesis-repair-mutation --goal "reject hypothesis science mutation"
if FAKE_HYPOTHESIS_CASE=claim-reference FAKE_HYPOTHESIS_RECOVERY_MUTATION=science python3 researchctl.py run hypothesis-repair-mutation; then
  echo "expected hypothesis structural repair mutation to block" >&2
  exit 1
fi

python3 researchctl.py new missing-result-study --goal "classify missing phase result"
if FAKE_SKIP_PHASE_RESULT=framing python3 researchctl.py run missing-result-study; then
  echo "expected missing phase result to block after bounded attempts" >&2
  exit 1
fi
python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/missing-result-study")
events=[json.loads(x) for x in (p/".research/events.jsonl").read_text().splitlines()]
failed=[x for x in events if x["event"] == "phase_failed"]
assert len(failed) == 2 and all(x["failure_class"] == "missing_phase_result" for x in failed)
archives=list((p/".research/invalid/framing").glob("*/failure.json")); assert len(archives) == 2
assert all(json.load(open(x))["failure_class"] == "missing_phase_result" for x in archives)
PY

python3 researchctl.py new mailbox-study --goal "reject mailbox provenance"
if FAKE_INCLUDE_MAILBOX_ARTIFACT=framing python3 researchctl.py run mailbox-study; then
  echo "expected mutable mailbox artifact declaration to block" >&2
  exit 1
fi
test ! -e projects/mailbox-study/.research/provenance/.research/phase-result.json.provenance.json

echo "hypothesis recovery test: PASS"
