#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

python3 -W error -m py_compile researchctl.py runtime/executor.py runtime/local_executor.py runtime/guarded_runner.py tests/bin/opencode
python3 -m json.tool workflow/research-workflow.json >/dev/null
python3 -m json.tool schemas/phase-result.schema.json >/dev/null
python3 tests/static_policy_test.py
python3 tests/local_executor_test.py
tests/upstream_skills_test.sh
tests/smoke_test.sh
tests/semantic_failure_test.sh
tests/freeze_tamper_test.sh
tests/environment_lock_tamper_test.sh
tests/recovery_lock_test.sh
tests/guarded_runner_test.sh
tests/local_executor_recovery_test.sh
tests/approval_budget_test.sh
tests/evidence_replay_test.sh
tests/evidence_repair_test.sh
tests/experiment_plan_repair_test.sh
tests/hypothesis_recovery_test.sh
tests/provenance_invalidation_test.sh
tests/skill_lock_test.sh
echo "all Research Mode tests: PASS"
