#!/usr/bin/env bash
set -euo pipefail

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
cp -a "$(cd "$(dirname "$0")/.." && pwd)" "$test_root/research-mode"
cd "$test_root/research-mode"
export PATH="$PWD/tests/bin:$PATH"

python3 researchctl.py doctor
printf '\nunauthorized mutation\n' >> .agents/skills/hypothesis-generation/SKILL.md
set +e
python3 researchctl.py doctor >doctor.out 2>&1
rc=$?
set -e
test "$rc" -eq 1
grep -q 'enabled skill hash mismatch: hypothesis-generation' doctor.out
echo "skill lock test: PASS"
