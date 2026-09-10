#!/usr/bin/env bash
set -euo pipefail

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
cp -a "$(cd "$(dirname "$0")/.." && pwd)" "$test_root/research-mode"
cd "$test_root/research-mode"
export PATH="$PWD/tests/bin:$PATH"

python3 researchctl.py new guard-study --goal "guard test"
python3 - <<'PY'
import hashlib, json, platform, sys
from pathlib import Path

p = Path("projects/guard-study")
script = p / "scripts/execute-approved.py"
script.write_text(r'''#!/usr/bin/env python3
import argparse,json
from pathlib import Path
q=argparse.ArgumentParser(); q.add_argument("--trial-spec",required=True); q.add_argument("--result",required=True); a=q.parse_args()
s=json.load(open(a.trial_spec)); counts_path=Path("runs/formal-experiment/invocations.json")
counts=json.load(open(counts_path)) if counts_path.exists() else {}
counts[s["trial_id"]]=counts.get(s["trial_id"],0)+1
counts_path.parent.mkdir(parents=True,exist_ok=True); counts_path.write_text(json.dumps(counts))
Path(a.result).write_text(json.dumps({"schema_version":"0.4.0-alpha.4","execution_id":s["execution_id"],"trial_id":s["trial_id"],"idempotency_key":s["idempotency_key"],"outcome":"completed","cost_usd":0.1,"wall_seconds":0.01,"metrics":{"pass":1},"artifacts":[]})+"\n")
''')
(p / "tasks/T1").mkdir(parents=True, exist_ok=True)
(p / "tasks/T1/task.txt").write_text("task\n")
sha=lambda x: hashlib.sha256(x.read_bytes()).hexdigest()
budget={"max_cost_usd":1.0,"max_wall_seconds":30,"max_run_seconds":5,"max_retries":1,"max_sessions":4}
manifest={
 "schema_version":"0.4.0-alpha.4","execution_id":"EXEC-GUARD-001",
 "executor":{"type":"local","protocol_version":"1","max_parallel":1,"failure_policy":"continue"},
 "entrypoint":["python3","scripts/execute-approved.py"],
 "trials":[{"trial_id":"trial-a","condition":"A","task_id":"T1","seed":1,"parameters":{}},{"trial_id":"trial-b","condition":"B","task_id":"T1","seed":1,"parameters":{}}],
 "estimated_sessions":2,"budget":budget,"frozen_inputs":["scripts","tasks"],
 "environment":{"python":{"version":f"{sys.version_info.major}.{sys.version_info.minor}","executable":"python3"},"dependency_lock":{"path":None,"sha256":None,"reason":"stdlib-only"},"code_snapshot":[{"path":"scripts/execute-approved.py","sha256":sha(script)}],"data_artifacts":[{"path":"tasks/T1/task.txt","sha256":sha(p/'tasks/T1/task.txt')}],"pass_env":[]}}
manifest_path=p/"configs/execution-manifest.json"; manifest_path.write_text(json.dumps(manifest))
env={"schema_version":"0.4.0-alpha.4","execution_id":"EXEC-GUARD-001","captured_at":"2026-09-06T00:00:00Z","executor":manifest["executor"],"runtime":{"python_executable":sys.executable,"python_version":platform.python_version(),"python_implementation":platform.python_implementation(),"platform":platform.platform(),"machine":platform.machine()},"declared_python":manifest["environment"]["python"],"dependency_lock_reason":"stdlib-only","pass_env_names":[],"files":[{"role":"code","path":"scripts/execute-approved.py","sha256":sha(script)},{"role":"data","path":"tasks/T1/task.txt","sha256":sha(p/'tasks/T1/task.txt')}]}
env_lock=p/".research/environment-lock.json"; env_lock.write_text(json.dumps(env))
frozen_path=p/".research/frozen-manifest.json"; frozen_path.write_text(json.dumps({"files":{"scripts/execute-approved.py":sha(script),"configs/execution-manifest.json":sha(manifest_path)}}))
auth={"schema_version":"0.4.0-alpha.4","execution_id":"EXEC-GUARD-001","budget":budget,"execution_manifest_sha256":sha(manifest_path),"environment_lock_sha256":sha(env_lock),"frozen_manifest_sha256":sha(frozen_path)}
(p/".research/execution-authorization.json").write_text(json.dumps(auth))
PY

(cd projects/guard-study && python3 .research/runtime/guarded_runner.py)
(cd projects/guard-study && python3 .research/runtime/guarded_runner.py)
python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/guard-study/runs/formal-experiment")
assert json.load(open(p/"invocations.json")) == {"trial-a": 1, "trial-b": 1}
s=json.load(open(p/"executor-state.json")); assert s["sessions_started"] == 2 and s["status"] == "completed"
r=json.load(open(p/"result.json")); assert r["outcome"] == "completed" and len(r["result_hashes"]) == 2
PY

printf '\n' >> projects/guard-study/.research/environment-lock.json
if (cd projects/guard-study && python3 .research/runtime/guarded_runner.py); then
  echo "expected environment-lock tamper to block execution" >&2
  exit 1
fi
python3 -c 'import json; r=json.load(open("projects/guard-study/runs/formal-experiment/guard-report.json")); assert r["status"] == "blocked" and "environment lock hash" in r["reason"]'

cp -a projects/guard-study projects/guard-cost-study
python3 - <<'PY'
import hashlib,json,platform,sys
from pathlib import Path
p=Path("projects/guard-cost-study")
for path in (p/"runs/formal-experiment").glob("*"):
    if path.is_file(): path.unlink()
script=p/"scripts/execute-approved.py"
script.write_text(script.read_text().replace('"cost_usd":0.1','"cost_usd":1.5'))
sha=lambda x: hashlib.sha256(x.read_bytes()).hexdigest()
m=json.load(open(p/"configs/execution-manifest.json"))
m["execution_id"]="EXEC-COST-001"; m["trials"]=m["trials"][:1]; m["estimated_sessions"]=1
m["environment"]["code_snapshot"][0]["sha256"]=sha(script)
mp=p/"configs/execution-manifest.json"; mp.write_text(json.dumps(m))
env={"schema_version":"0.4.0-alpha.4","execution_id":"EXEC-COST-001","captured_at":"2026-09-06T00:00:00Z","executor":m["executor"],"runtime":{"python_executable":sys.executable,"python_version":platform.python_version(),"python_implementation":platform.python_implementation(),"platform":platform.platform(),"machine":platform.machine()},"declared_python":m["environment"]["python"],"dependency_lock_reason":"stdlib-only","pass_env_names":[],"files":[{"role":"code","path":"scripts/execute-approved.py","sha256":sha(script)},{"role":"data","path":"tasks/T1/task.txt","sha256":sha(p/'tasks/T1/task.txt')}]}
ep=p/".research/environment-lock.json"; ep.write_text(json.dumps(env))
fp=p/".research/frozen-manifest.json"; fp.write_text(json.dumps({"files":{"scripts/execute-approved.py":sha(script),"configs/execution-manifest.json":sha(mp)}}))
auth={"schema_version":"0.4.0-alpha.4","execution_id":"EXEC-COST-001","budget":m["budget"],"execution_manifest_sha256":sha(mp),"environment_lock_sha256":sha(ep),"frozen_manifest_sha256":sha(fp)}
(p/".research/execution-authorization.json").write_text(json.dumps(auth))
PY
if (cd projects/guard-cost-study && python3 .research/runtime/guarded_runner.py); then
  echo "expected post-trial cost guard to terminate execution" >&2
  exit 1
fi
python3 -c 'import json; r=json.load(open("projects/guard-cost-study/runs/formal-experiment/guard-report.json")); assert r["status"] == "terminated" and "cost" in r["reason"]'

echo "guarded runner and idempotency test: PASS"
