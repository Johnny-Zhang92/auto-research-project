#!/usr/bin/env bash
set -euo pipefail

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
cp -a "$(cd "$(dirname "$0")/.." && pwd)" "$test_root/research-mode"
cd "$test_root/research-mode"
export PATH="$PWD/tests/bin:$PATH"

python3 researchctl.py doctor
python3 researchctl.py new test-study --goal "offline workflow transition test"
test -f projects/test-study/.research/runtime/paper-lookup/paginate.py
test -f projects/test-study/.research/runtime/paper-lookup/arxiv_atom.py
python3 researchctl.py run test-study
python3 -c 'import json; assert json.load(open("projects/test-study/.research/state.json"))["phase"] == "plan-approval"'
python3 researchctl.py approve test-study --note "test approval"
python3 researchctl.py run test-study
python3 -c 'import json; assert json.load(open("projects/test-study/.research/state.json"))["phase"] == "execution-approval"'
test ! -f projects/test-study/.research/frozen-manifest.json
python3 researchctl.py approve test-study --note "execution approval"
test -f projects/test-study/.research/frozen-manifest.json
test -f projects/test-study/.research/environment-lock.json
python3 -c 'import json; a=json.load(open("projects/test-study/.research/execution-authorization.json")); assert a["environment_lock_sha256"] and a["execution_id"] == "EXEC-OFFLINE-001"'
(cd projects/test-study && python3 .research/runtime/guarded_runner.py)
python3 -c 'import json; r=json.load(open("projects/test-study/runs/formal-experiment/result.json")); assert r["outcome"] == "completed" and len(r["result_hashes"]) == 8'
python3 researchctl.py run test-study
python3 -c 'import json; s=json.load(open("projects/test-study/.research/state.json")); assert s["phase"] == "done" and s["status"] == "completed"'
test -f projects/test-study/report/final-report.md
python3 researchctl.py audit test-study
python3 -c 'import json; r=json.load(open("projects/test-study/research/audit-report.json")); assert r["status"] == "PASS"'
echo "offline smoke test: PASS"
