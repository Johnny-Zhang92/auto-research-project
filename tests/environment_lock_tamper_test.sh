#!/usr/bin/env bash
set -euo pipefail

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
cp -a "$(cd "$(dirname "$0")/.." && pwd)" "$test_root/research-mode"
cd "$test_root/research-mode"
export PATH="$PWD/tests/bin:$PATH"

python3 researchctl.py new environment-study --goal "environment authorization test"
python3 researchctl.py run environment-study
python3 researchctl.py approve environment-study --note "plan approval"
python3 researchctl.py run environment-study
python3 researchctl.py approve environment-study --note "execution approval"
printf '\n' >> projects/environment-study/.research/environment-lock.json
if python3 researchctl.py run environment-study; then
  echo "expected environment lock tamper to block" >&2
  exit 1
fi
python3 - <<'PY'
import json
s=json.load(open("projects/environment-study/.research/state.json"))
assert s["phase"] == "blocked"
assert "environment_lock_sha256" in s["blocker"]
PY

echo "environment lock tamper test: PASS"
