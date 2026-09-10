#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from local_executor import LocalExecutor  # noqa: E402
from executor import ExecutionBlocked  # noqa: E402


VERSION = (ROOT / "VERSION").read_text().strip()


class LocalExecutorTests(unittest.TestCase):
    def project(self, script: str, max_retries: int = 2, max_run: float = 1.0):
        temporary = tempfile.TemporaryDirectory()
        project = Path(temporary.name)
        (project / "configs").mkdir()
        (project / "scripts").mkdir()
        (project / "scripts/execute-approved.py").write_text(script)
        manifest = {
            "schema_version": VERSION,
            "execution_id": "EXEC-UNIT-001",
            "executor": {"type": "local", "protocol_version": "1", "max_parallel": 1, "failure_policy": "continue"},
            "entrypoint": ["python3", "scripts/execute-approved.py"],
            "trials": [{"trial_id": "trial-1", "condition": "A", "task_id": "T1", "seed": 1, "parameters": {}}],
            "estimated_sessions": 1,
            "budget": {"max_cost_usd": 2.0, "max_wall_seconds": 10, "max_run_seconds": max_run, "max_retries": max_retries, "max_sessions": 1 + max_retries},
            "frozen_inputs": ["scripts"],
            "environment": {"pass_env": []},
        }
        (project / "configs/execution-manifest.json").write_text(json.dumps(manifest))
        authorization = {"budget": manifest["budget"]}
        return temporary, project, manifest, authorization

    def test_invalid_result_is_not_retried(self) -> None:
        script = r'''import argparse,json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument("--trial-spec"); p.add_argument("--result"); a=p.parse_args()
m=Path("count.txt"); m.write_text(str(int(m.read_text())+1) if m.exists() else "1")
Path(a.result).write_text(json.dumps({"invalid": True}))
'''
        temporary, project, manifest, authorization = self.project(script)
        self.addCleanup(temporary.cleanup)
        result = LocalExecutor(project, manifest, authorization, VERSION).execute()
        state = json.loads((project / "runs/formal-experiment/executor-state.json").read_text())
        record = state["trials"]["trial-1"]
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertEqual((project / "count.txt").read_text(), "1")
        self.assertEqual(record["attempts"], 1)
        self.assertEqual(record["attempt_history"][0]["failure_class"], "invalid_result")

    def test_timeout_retries_once_and_preserves_attempts(self) -> None:
        script = f'''import argparse,json,time
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument("--trial-spec"); p.add_argument("--result"); a=p.parse_args()
s=json.load(open(a.trial_spec)); m=Path("count.txt"); count=int(m.read_text())+1 if m.exists() else 1; m.write_text(str(count))
if count == 1: time.sleep(1)
else: Path(a.result).write_text(json.dumps({{"schema_version":"{VERSION}","execution_id":s["execution_id"],"trial_id":s["trial_id"],"idempotency_key":s["idempotency_key"],"outcome":"completed","cost_usd":0.1,"wall_seconds":0.01,"metrics":{{}},"artifacts":[]}}))
'''
        temporary, project, manifest, authorization = self.project(script, max_retries=1, max_run=0.15)
        self.addCleanup(temporary.cleanup)
        executor = LocalExecutor(project, manifest, authorization, VERSION)
        result = executor.execute()
        state = json.loads((project / "runs/formal-experiment/executor-state.json").read_text())
        record = state["trials"]["trial-1"]
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual((project / "count.txt").read_text(), "2")
        self.assertEqual(record["attempts"], 2)
        self.assertEqual(record["attempt_history"][0]["failure_class"], "timeout")
        trial_dir = project / "runs/formal-experiment/trials/trial-1"
        self.assertTrue((trial_dir / "attempt-1.input.json").is_file())
        self.assertTrue((trial_dir / "attempt-1.log").is_file())
        self.assertTrue((trial_dir / "attempt-2.input.json").is_file())
        self.assertTrue((trial_dir / "attempt-2.result.json").is_file())
        LocalExecutor(project, manifest, authorization, VERSION).execute()
        self.assertEqual((project / "count.txt").read_text(), "2")

    def test_live_stale_pid_blocks_duplicate_trial(self) -> None:
        temporary, project, manifest, authorization = self.project("raise SystemExit(1)")
        self.addCleanup(temporary.cleanup)
        executor = LocalExecutor(project, manifest, authorization, VERSION)
        state = executor._new_state()
        state["status"] = "running"
        state["sessions_started"] = 1
        state["trials"]["trial-1"].update(status="running", attempts=1, pid=os.getpid())
        executor._save_state(state)
        with self.assertRaisesRegex(ExecutionBlocked, "live process"):
            executor.execute()


if __name__ == "__main__":
    unittest.main()
