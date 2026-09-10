#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

python3 -m py_compile \
  .agents/skills/paper-lookup/scripts/*.py \
  .agents/skills/experimental-design/scripts/*.py \
  .agents/skills/peer-review/scripts/*.py

python3 .agents/skills/paper-lookup/scripts/paginate.py --list-apis >/dev/null
python3 .agents/skills/paper-lookup/scripts/paginate.py \
  --api openalex --query 'search=agent+workflow' --max-records 5 --dry-run \
  | grep -q 'api.openalex.org'

python3 - <<'PY'
import sys
sys.path.insert(0, '.agents/skills/experimental-design/scripts')
from randomization import block_randomization

schedule = block_randomization(12, arms=['A', 'B'], seed=42)
assert len(schedule) == 12
assert set(schedule['arm']) == {'A', 'B'}
assert schedule.groupby('block')['arm'].value_counts().min() >= 1
PY

temp_dir="$(mktemp -d)"
trap 'rm -rf "$temp_dir"' EXIT
cat >"$temp_dir/intake.json" <<'JSON'
{
  "schema_version": "2.0",
  "review_id": "RESEARCH-INTERNAL-001",
  "material": {"status": "synthetic", "contains_personal_or_sensitive_data": false},
  "authorization": {"basis": "synthetic_training", "documented": true, "local_processing_authorized": true, "external_processing_authorized": false},
  "reviewer": {"capacity": "training", "human_accountable": true, "competence_areas": ["research_methods"], "competence_limits": [], "conflict_status": "none_identified", "conflicts": []},
  "venue_policy": {"checked": true, "peer_review_model": "open", "confidential_editor_notes_supported": false},
  "ai_use": {"policy": "not_stated", "planned": "local_deterministic_tools", "permission_confirmed": false, "disclosure_planned": false},
  "handling": {"local_only": true, "external_service_use": false, "data_reuse_permitted": false, "retention_rule": "public_material", "deletion_or_retention_record_planned": true},
  "scope": {"manuscript_type": "computational_research_artifacts", "requested_focus": ["methods", "reproducibility"], "out_of_scope": [], "specialist_review_needed": []}
}
JSON
python3 .agents/skills/peer-review/scripts/validate_review_intake.py \
  "$temp_dir/intake.json" -o "$temp_dir/report.json"
python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))["status"] == "READY_FOR_LOCAL_REVIEW"' "$temp_dir/report.json"

echo "upstream skill admission tests: PASS"
