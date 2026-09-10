#!/usr/bin/env bash
set -euo pipefail

source_root="$(cd "$(dirname "$0")/.." && pwd)"

run_case() {
  local case_name="$1"
  local expected="$2"
  local test_root
  test_root="$(mktemp -d)"
  cp -a "$source_root" "$test_root/research-mode"
  (
    cd "$test_root/research-mode"
    export PATH="$PWD/tests/bin:$PATH"
    export FAKE_EVIDENCE_CASE="$case_name"
    python3 researchctl.py new "evidence-${case_name}" --goal "evidence replay fixture"
    set +e
    python3 researchctl.py run "evidence-${case_name}"
    rc=$?
    set -e
    phase="$(python3 -c "import json; print(json.load(open('projects/evidence-${case_name}/.research/state.json'))['phase'])")"
    test "$phase" = "$expected"
    if test "$expected" = "plan-approval"; then
      python3 researchctl.py audit "evidence-${case_name}"
    else
      test "$rc" -eq 2
    fi
  )
  rm -rf "$test_root"
}

run_case normal plan-approval
run_case contradictory plan-approval
run_case inaccessible plan-approval
run_case bad-snapshot-hash blocked
echo "evidence replay test: PASS"
