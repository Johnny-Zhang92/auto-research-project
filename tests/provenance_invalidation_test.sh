#!/usr/bin/env bash
set -euo pipefail

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
cp -a "$(cd "$(dirname "$0")/.." && pwd)" "$test_root/research-mode"
cd "$test_root/research-mode"
export PATH="$PWD/tests/bin:$PATH"

python3 researchctl.py new provenance-study --goal "provenance invalidation fixture"
python3 researchctl.py run provenance-study
python3 researchctl.py audit provenance-study

python3 - <<'PY'
import json
from pathlib import Path
path = Path("projects/provenance-study/research/evidence-ledger.json")
data = json.loads(path.read_text())
data["claims"][0]["text"] = "tampered but structurally valid claim"
path.write_text(json.dumps(data, indent=2) + "\n")
PY

set +e
python3 researchctl.py audit provenance-study
audit_rc=$?
set -e
test "$audit_rc" -eq 2
python3 -c 'import json; r=json.load(open("projects/provenance-study/research/audit-report.json")); assert r["status"] == "FAIL" and any("hash mismatch" in e or "upstream input changed" in e for e in r["errors"])'

set +e
python3 researchctl.py approve provenance-study --note "test stale provenance handling" >approve.out 2>&1
approve_rc=$?
set -e
test "$approve_rc" -eq 1
grep -q 'approval refused because provenance is invalid' approve.out
python3 -c 'import json; s=json.load(open("projects/provenance-study/.research/state.json")); assert s["phase"] == "plan-approval" and s["status"] == "waiting_approval"'
echo "provenance invalidation test: PASS"
