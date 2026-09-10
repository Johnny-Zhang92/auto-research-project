#!/usr/bin/env bash
set -euo pipefail

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
cp -a "$(cd "$(dirname "$0")/.." && pwd)" "$test_root/research-mode"
cd "$test_root/research-mode"
export PATH="$PWD/tests/bin:$PATH"

python3 researchctl.py new recovery-trial --goal "trial recovery test"
python3 - <<'PY'
import hashlib,json,platform,sys
from pathlib import Path
p=Path("projects/recovery-trial")
script=p/"scripts/execute-approved.py"
script.write_text(r'''#!/usr/bin/env python3
import argparse,json
from pathlib import Path
q=argparse.ArgumentParser(); q.add_argument("--trial-spec",required=True); q.add_argument("--result",required=True); a=q.parse_args()
s=json.load(open(a.trial_spec)); marker=Path("runs/formal-experiment/invocations.txt")
marker.parent.mkdir(parents=True,exist_ok=True); marker.write_text(marker.read_text()+"x" if marker.exists() else "x")
Path(a.result).write_text(json.dumps({"schema_version":"0.4.0-alpha.4","execution_id":s["execution_id"],"trial_id":s["trial_id"],"idempotency_key":s["idempotency_key"],"outcome":"completed","cost_usd":0.1,"wall_seconds":0.01,"metrics":{},"artifacts":[]})+"\n")
''')
sha=lambda x:hashlib.sha256(x.read_bytes()).hexdigest()
budget={"max_cost_usd":1.0,"max_wall_seconds":30,"max_run_seconds":5,"max_retries":1,"max_sessions":2}
trial={"trial_id":"trial-recover","condition":"A","task_id":"T1","seed":7,"parameters":{}}
manifest={"schema_version":"0.4.0-alpha.4","execution_id":"EXEC-RECOVER-001","executor":{"type":"local","protocol_version":"1","max_parallel":1,"failure_policy":"continue"},"entrypoint":["python3","scripts/execute-approved.py"],"trials":[trial],"estimated_sessions":1,"budget":budget,"frozen_inputs":["scripts"],"environment":{"python":{"version":f"{sys.version_info.major}.{sys.version_info.minor}","executable":"python3"},"dependency_lock":{"path":None,"sha256":None,"reason":"stdlib-only"},"code_snapshot":[{"path":"scripts/execute-approved.py","sha256":sha(script)}],"data_artifacts":[],"pass_env":[]}}
mp=p/"configs/execution-manifest.json"; mp.write_text(json.dumps(manifest))
env={"schema_version":"0.4.0-alpha.4","execution_id":"EXEC-RECOVER-001","captured_at":"2026-09-06T00:00:00Z","executor":manifest["executor"],"runtime":{"python_executable":sys.executable,"python_version":platform.python_version(),"python_implementation":platform.python_implementation(),"platform":platform.platform(),"machine":platform.machine()},"declared_python":manifest["environment"]["python"],"dependency_lock_reason":"stdlib-only","pass_env_names":[],"files":[{"role":"code","path":"scripts/execute-approved.py","sha256":sha(script)}]}
ep=p/".research/environment-lock.json"; ep.write_text(json.dumps(env))
fp=p/".research/frozen-manifest.json"; fp.write_text(json.dumps({"files":{"scripts/execute-approved.py":sha(script),"configs/execution-manifest.json":sha(mp)}}))
auth={"schema_version":"0.4.0-alpha.4","execution_id":"EXEC-RECOVER-001","budget":budget,"execution_manifest_sha256":sha(mp),"environment_lock_sha256":sha(ep),"frozen_manifest_sha256":sha(fp)}
(p/".research/execution-authorization.json").write_text(json.dumps(auth))
state={"schema_version":"0.4.0-alpha.4","execution_id":"EXEC-RECOVER-001","manifest_sha256":sha(mp),"status":"running","created_at":"2026-09-06T00:00:00Z","updated_at":"2026-09-06T00:00:00Z","sessions_started":1,"cost_usd":0.0,"interruptions":0,"trials":{"trial-recover":{"status":"running","attempts":1,"pid":99999999,"result_sha256":None,"last_error":None,"updated_at":"2026-09-06T00:00:00Z"}}}
sp=p/"runs/formal-experiment/executor-state.json"; sp.parent.mkdir(parents=True,exist_ok=True); sp.write_text(json.dumps(state))
PY

cp -a projects/recovery-trial projects/recovered-result
python3 - <<'PY'
import hashlib,json
from pathlib import Path
p=Path("projects/recovered-result")
manifest=json.load(open(p/"configs/execution-manifest.json"))
manifest_hash=hashlib.sha256((p/"configs/execution-manifest.json").read_bytes()).hexdigest()
trial=manifest["trials"][0]
payload=json.dumps({"execution_id":manifest["execution_id"],"manifest_sha256":manifest_hash,"trial":trial},sort_keys=True,separators=(",",":")).encode()
key=hashlib.sha256(payload).hexdigest()
result={"schema_version":"0.4.0-alpha.4","execution_id":manifest["execution_id"],"trial_id":trial["trial_id"],"idempotency_key":key,"outcome":"completed","cost_usd":0.2,"wall_seconds":0.1,"metrics":{},"artifacts":[]}
rp=p/"runs/formal-experiment/trials/trial-recover/attempt-1.result.json"
rp.parent.mkdir(parents=True,exist_ok=True); rp.write_text(json.dumps(result))
PY

(cd projects/recovery-trial && python3 .research/runtime/guarded_runner.py)
(cd projects/recovery-trial && python3 .research/runtime/guarded_runner.py)
python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/recovery-trial/runs/formal-experiment")
s=json.load(open(p/"executor-state.json"))
assert s["interruptions"] == 1
assert s["sessions_started"] == 2
assert s["trials"]["trial-recover"]["attempts"] == 2
assert (p/"invocations.txt").read_text() == "x"
assert (p/"trials/trial-recover/attempt-2.log").is_file()
PY

(cd projects/recovered-result && python3 .research/runtime/guarded_runner.py)
python3 - <<'PY'
import json
from pathlib import Path
p=Path("projects/recovered-result/runs/formal-experiment")
s=json.load(open(p/"executor-state.json"))
assert s["interruptions"] == 1 and s["sessions_started"] == 1
assert s["trials"]["trial-recover"]["status"] == "completed"
assert not (p/"invocations.txt").exists()
assert json.load(open(p/"result.json"))["cost_usd"] == 0.2
PY

echo "local executor stale-trial recovery test: PASS"
