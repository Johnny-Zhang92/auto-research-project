#!/usr/bin/env bash
set -euo pipefail

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
cp -a "$(cd "$(dirname "$0")/.." && pwd)" "$test_root/research-mode"
cd "$test_root/research-mode"
export PATH="$PWD/tests/bin:$PATH"

python3 researchctl.py new recovery-study --goal "recovery test"
python3 - <<'PY'
import json
p = "projects/recovery-study/.research/state.json"
s = json.load(open(p))
s["status"] = "running"
s["active_run"] = {"run_id": "dead-run", "pid": 999999, "phase": "framing", "started_at": "2026-01-01T00:00:00+00:00"}
json.dump(s, open(p, "w"), indent=2)
PY
python3 researchctl.py run recovery-study --max-steps 1
python3 -c 'import json; s=json.load(open("projects/recovery-study/.research/state.json")); assert s["phase"] == "evidence" and s["interruptions"] == 1'
grep -q '"event": "phase_recovered"' projects/recovery-study/.research/events.jsonl

python3 -c 'import time; from pathlib import Path; import researchctl; p=Path("projects/recovery-study"); lock=researchctl.ProjectLock(p); lock.__enter__(); time.sleep(4)' &
holder=$!
sleep 1
if python3 researchctl.py status recovery-study >/dev/null; then
  true
fi
if python3 researchctl.py run recovery-study --max-steps 1 2>lock-error.txt; then
  echo "expected concurrent runner rejection" >&2
  exit 1
fi
grep -q "another researchctl process holds" lock-error.txt
wait "$holder"
echo "recovery and lock test: PASS"
