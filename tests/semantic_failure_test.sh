#!/usr/bin/env bash
set -euo pipefail

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
cp -a "$(cd "$(dirname "$0")/.." && pwd)" "$test_root/research-mode"
cd "$test_root/research-mode"
export PATH="$PWD/tests/bin:$PATH"
export FAKE_INVALID_PHASE=framing

python3 researchctl.py new invalid-study --goal "validator failure test"
if python3 researchctl.py run invalid-study; then
  echo "expected invalid project to block" >&2
  exit 1
fi
python3 -c 'import json; s=json.load(open("projects/invalid-study/.research/state.json")); assert s["phase"] == "blocked" and "semantic validation failed" in s["blocker"]'
test "$(grep -c '"event": "phase_failed"' projects/invalid-study/.research/events.jsonl)" -eq 2
echo "semantic failure test: PASS"
