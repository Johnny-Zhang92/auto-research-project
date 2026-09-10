#!/usr/bin/env bash
set -euo pipefail

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
cp -a "$(cd "$(dirname "$0")/.." && pwd)" "$test_root/research-mode"
cd "$test_root/research-mode"
export PATH="$PWD/tests/bin:$PATH"

python3 researchctl.py new budget-study --goal "approval budget test"
python3 researchctl.py run budget-study
python3 researchctl.py approve budget-study --note "plan approval"
export FAKE_PROJECTED_COST=2.0
python3 researchctl.py run budget-study
if python3 researchctl.py approve budget-study --note "should fail" 2>approval-error.txt; then
  echo "expected over-budget approval rejection" >&2
  exit 1
fi
grep -q "projected cost exceeds approved budget" approval-error.txt
python3 -c 'import json; s=json.load(open("projects/budget-study/.research/state.json")); assert s["phase"] == "execution-approval"'
test ! -f projects/budget-study/.research/execution-authorization.json
echo "approval budget test: PASS"
