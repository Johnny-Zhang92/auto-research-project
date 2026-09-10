#!/usr/bin/env bash
set -euo pipefail

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
cp -a "$(cd "$(dirname "$0")/.." && pwd)" "$test_root/research-mode"
cd "$test_root/research-mode"
export PATH="$PWD/tests/bin:$PATH"

python3 researchctl.py new tamper-study --goal "freeze test"
python3 researchctl.py run tamper-study
python3 researchctl.py approve tamper-study --note "plan approval"
python3 researchctl.py run tamper-study
python3 researchctl.py approve tamper-study --note "execution approval"
printf '\nTAMPER\n' >> projects/tamper-study/scripts/execute-approved.py
if python3 researchctl.py run tamper-study; then
  echo "expected frozen-input tamper to block" >&2
  exit 1
fi
python3 -c 'import json; s=json.load(open("projects/tamper-study/.research/state.json")); assert s["phase"] == "blocked" and "frozen inputs changed" in s["blocker"]'
echo "freeze tamper test: PASS"
