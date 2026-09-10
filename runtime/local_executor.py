#!/usr/bin/env python3
"""Sequential, checkpointed and idempotent local trial executor."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from executor import ExecutionBlocked, ExecutionTerminated, Executor


TRIAL_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
SAFE_ENV = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def atomic_object(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_group(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


class LocalExecutor(Executor):
    """Execute frozen trials locally, one at a time, with durable checkpoints."""

    def __init__(
        self,
        project: Path,
        manifest: dict[str, Any],
        authorization: dict[str, Any],
        schema_version: str,
    ) -> None:
        super().__init__(project, manifest, authorization)
        self.schema_version = schema_version
        self.execution_id = str(manifest["execution_id"])
        self.root = self.project / "runs" / "formal-experiment"
        self.state_path = self.root / "executor-state.json"
        self.result_path = self.root / "result.json"
        self.lock_path = self.root / "executor.lock"
        self.manifest_hash = file_sha256(
            self.project / "configs" / "execution-manifest.json"
        )
        self.budget = authorization["budget"]

    def _new_state(self) -> dict[str, Any]:
        trials = {
            item["trial_id"]: {
                "status": "pending",
                "attempts": 0,
                "attempt_history": [],
                "pid": None,
                "result_sha256": None,
                "result_path": None,
                "last_error": None,
                "updated_at": now(),
            }
            for item in self.manifest["trials"]
        }
        return {
            "schema_version": self.schema_version,
            "execution_id": self.execution_id,
            "manifest_sha256": self.manifest_hash,
            "status": "ready",
            "created_at": now(),
            "updated_at": now(),
            "sessions_started": 0,
            "cost_usd": 0.0,
            "wall_seconds": 0.0,
            "interruptions": 0,
            "trials": trials,
        }

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            state = self._new_state()
            atomic_object(self.state_path, state)
            return state
        state = read_object(self.state_path)
        if state.get("schema_version") != self.schema_version:
            raise ExecutionBlocked("executor checkpoint schema version differs")
        if state.get("execution_id") != self.execution_id:
            raise ExecutionBlocked("executor checkpoint belongs to another execution")
        if state.get("manifest_sha256") != self.manifest_hash:
            raise ExecutionBlocked("executor checkpoint manifest hash differs")
        expected = {item["trial_id"] for item in self.manifest["trials"]}
        if set(state.get("trials", {})) != expected:
            raise ExecutionBlocked("executor checkpoint trial set differs")
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = now()
        atomic_object(self.state_path, state)

    def _recover(self, state: dict[str, Any]) -> None:
        changed = False
        for trial_id, record in state["trials"].items():
            if record.get("status") != "running":
                continue
            if pid_alive(record.get("pid")):
                raise ExecutionBlocked(
                    f"trial {trial_id} still has a live process; refusing duplicate execution"
                )
            trial = next(item for item in self.manifest["trials"] if item["trial_id"] == trial_id)
            _, result_path, _ = self._trial_paths(trial_id, int(record["attempts"]))
            recovered: dict[str, Any] | None = None
            if result_path.is_file():
                try:
                    recovered = self._validate_trial_result(
                        result_path, trial_id, self._idempotency_key(trial)
                    )
                except (OSError, ValueError, json.JSONDecodeError, ExecutionBlocked):
                    recovered = None
            if recovered is not None:
                state["cost_usd"] = float(state.get("cost_usd", 0.0)) + float(recovered["cost_usd"])
                state["wall_seconds"] = float(state.get("wall_seconds", 0.0)) + float(recovered["wall_seconds"])
                record.update(
                    status="completed",
                    pid=None,
                    result_sha256=file_sha256(result_path),
                    result_path=str(result_path.relative_to(self.project)),
                    last_error=None,
                    updated_at=now(),
                )
                record.setdefault("attempt_history", []).append(
                    {
                        "attempt": int(record["attempts"]),
                        "recovered": True,
                        "return_code": 0,
                        "result_sha256": file_sha256(result_path),
                        "error": None,
                    }
                )
            else:
                record.update(
                    status="pending",
                    pid=None,
                    last_error="previous executor ended while trial was running",
                    updated_at=now(),
                )
            state["interruptions"] = int(state.get("interruptions", 0)) + 1
            changed = True
        if changed:
            self._save_state(state)

    def _safe_relative(self, relative: Any, prefix: str | None = None) -> Path:
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise ExecutionBlocked(f"unsafe project-relative path: {relative!r}")
        if prefix and not relative.startswith(prefix):
            raise ExecutionBlocked(f"path must start with {prefix}: {relative}")
        target = (self.project / relative).resolve()
        try:
            target.relative_to(self.project)
        except ValueError as exc:
            raise ExecutionBlocked(f"path escapes project: {relative}") from exc
        return target

    def _trial_paths(self, trial_id: str, attempt: int) -> tuple[Path, Path, Path]:
        directory = self.root / "trials" / trial_id
        return (
            directory / f"attempt-{attempt}.input.json",
            directory / f"attempt-{attempt}.result.json",
            directory / f"attempt-{attempt}.log",
        )

    def _idempotency_key(self, trial: dict[str, Any]) -> str:
        payload = json.dumps(
            {
                "execution_id": self.execution_id,
                "manifest_sha256": self.manifest_hash,
                "trial": trial,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _environment(self, trial_id: str, key: str) -> dict[str, str]:
        source = os.environ
        result = {
            name: source[name]
            for name in ("PATH", "LANG", "LC_ALL", "TZ", "HOME")
            if name in source
        }
        for name in self.manifest["environment"].get("pass_env", []):
            if not SAFE_ENV.fullmatch(name):
                raise ExecutionBlocked(f"invalid pass_env name: {name!r}")
            if name not in source:
                raise ExecutionBlocked(f"approved environment variable is missing: {name}")
            result[name] = source[name]
        result.update(
            {
                "RESEARCH_EXECUTION_ID": self.execution_id,
                "RESEARCH_TRIAL_ID": trial_id,
                "RESEARCH_IDEMPOTENCY_KEY": key,
                "RESEARCH_MAX_COST_USD": str(self.budget["max_cost_usd"]),
                "RESEARCH_MAX_RUN_SECONDS": str(self.budget["max_run_seconds"]),
            }
        )
        return result

    def _validate_trial_result(
        self, path: Path, trial_id: str, key: str
    ) -> dict[str, Any]:
        result = read_object(path)
        required = {
            "schema_version",
            "execution_id",
            "trial_id",
            "idempotency_key",
            "outcome",
            "cost_usd",
            "wall_seconds",
            "metrics",
            "artifacts",
        }
        if set(result) != required:
            raise ValueError("trial result keys do not match the protocol")
        if result["schema_version"] != self.schema_version:
            raise ValueError("trial result schema version differs")
        if result["execution_id"] != self.execution_id:
            raise ValueError("trial result execution_id differs")
        if result["trial_id"] != trial_id or result["idempotency_key"] != key:
            raise ValueError("trial result identity differs")
        if result["outcome"] not in {"completed", "failed", "inconclusive"}:
            raise ValueError("trial result outcome is invalid")
        for field in ("cost_usd", "wall_seconds"):
            value = result[field]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ValueError(f"trial result {field} must be non-negative")
        if not isinstance(result["metrics"], dict) or not isinstance(result["artifacts"], list):
            raise ValueError("trial result metrics/artifacts have invalid types")
        for artifact in result["artifacts"]:
            target = self._safe_relative(artifact, prefix="runs/formal-experiment/")
            if not target.is_file():
                raise ValueError(f"trial artifact is missing: {artifact}")
        return result

    def _completed_result(
        self, trial_id: str, record: dict[str, Any]
    ) -> dict[str, Any]:
        _, result_path, _ = self._trial_paths(trial_id, int(record["attempts"]))
        if not result_path.is_file():
            raise ExecutionBlocked(f"completed trial result is missing: {trial_id}")
        if file_sha256(result_path) != record.get("result_sha256"):
            raise ExecutionBlocked(f"completed trial result hash differs: {trial_id}")
        trial = next(item for item in self.manifest["trials"] if item["trial_id"] == trial_id)
        return self._validate_trial_result(result_path, trial_id, self._idempotency_key(trial))

    def _limits_before_start(self, state: dict[str, Any]) -> None:
        if int(state["sessions_started"]) >= int(self.budget["max_sessions"]):
            raise ExecutionTerminated("session budget reached")
        if float(state["cost_usd"]) >= float(self.budget["max_cost_usd"]):
            raise ExecutionTerminated("cost budget reached")
        if float(state.get("wall_seconds", 0.0)) >= float(self.budget["max_wall_seconds"]):
            raise ExecutionTerminated("wall-time budget reached")

    def _run_trial(
        self, state: dict[str, Any], trial: dict[str, Any]
    ) -> dict[str, Any] | None:
        trial_id = trial["trial_id"]
        if not TRIAL_ID.fullmatch(trial_id):
            raise ExecutionBlocked(f"invalid trial id: {trial_id!r}")
        record = state["trials"][trial_id]
        if record["status"] == "completed":
            return self._completed_result(trial_id, record)
        max_attempts = 1 + int(self.budget["max_retries"])
        last_parsed: dict[str, Any] | None = None
        last_parsed_path: Path | None = None
        while int(record["attempts"]) < max_attempts:
            self._limits_before_start(state)
            attempt = int(record["attempts"]) + 1
            input_path, result_path, log_path = self._trial_paths(trial_id, attempt)
            input_path.parent.mkdir(parents=True, exist_ok=True)
            key = self._idempotency_key(trial)
            atomic_object(
                input_path,
                {
                    "schema_version": self.schema_version,
                    "execution_id": self.execution_id,
                    "trial_id": trial_id,
                    "idempotency_key": key,
                    "condition": trial["condition"],
                    "task_id": trial["task_id"],
                    "seed": trial["seed"],
                    "parameters": trial.get("parameters", {}),
                },
            )
            if result_path.exists():
                result_path.unlink()
            # Run the trial with the exact interpreter that passed the
            # environment-lock check, not whichever `python3` appears first
            # in a later PATH lookup.
            command = [sys.executable, self.manifest["entrypoint"][1]] + [
                "--trial-spec",
                str(input_path.relative_to(self.project)),
                "--result",
                str(result_path.relative_to(self.project)),
            ]
            record.update(
                status="running",
                attempts=attempt,
                pid=None,
                last_error=None,
                updated_at=now(),
            )
            state["status"] = "running"
            state["sessions_started"] = int(state["sessions_started"]) + 1
            self._save_state(state)
            started = time.monotonic()
            with log_path.open("w", encoding="utf-8") as output:
                process = subprocess.Popen(
                    command,
                    cwd=self.project,
                    env=self._environment(trial_id, key),
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                record["pid"] = process.pid
                record["updated_at"] = now()
                self._save_state(state)
                try:
                    return_code = process.wait(timeout=float(self.budget["max_run_seconds"]))
                    timed_out = False
                except subprocess.TimeoutExpired:
                    terminate_group(process)
                    return_code = 124
                    timed_out = True
            elapsed = time.monotonic() - started
            state["wall_seconds"] = float(state.get("wall_seconds", 0.0)) + elapsed
            parsed: dict[str, Any] | None = None
            error: str | None = None
            if result_path.is_file():
                try:
                    parsed = self._validate_trial_result(result_path, trial_id, key)
                    last_parsed = parsed
                    last_parsed_path = result_path
                except (OSError, ValueError, json.JSONDecodeError, ExecutionBlocked) as exc:
                    error = f"invalid trial result: {exc}"
            else:
                error = "trial did not write result.json"
            if return_code != 0:
                error = f"trial exited {return_code}; {error or 'result preserved'}"
            failure_class = (
                "timeout"
                if timed_out
                else "process_exit"
                if return_code != 0
                else "invalid_result"
                if parsed is None
                else None
            )
            if parsed is not None:
                state["cost_usd"] = float(state["cost_usd"]) + float(parsed["cost_usd"])
            history = {
                "attempt": attempt,
                "recovered": False,
                "return_code": return_code,
                "result_sha256": file_sha256(result_path) if result_path.is_file() else None,
                "error": error,
                "failure_class": failure_class,
                "wall_seconds": elapsed,
            }
            record.setdefault("attempt_history", []).append(history)
            if return_code == 0 and parsed is not None:
                record.update(
                    status="completed",
                    pid=None,
                    result_sha256=file_sha256(result_path),
                    result_path=str(result_path.relative_to(self.project)),
                    last_error=None,
                    wall_seconds=elapsed,
                    updated_at=now(),
                )
                self._save_state(state)
                return parsed
            record.update(status="pending", pid=None, last_error=error, updated_at=now())
            self._save_state(state)
            if failure_class == "invalid_result":
                break
        record["status"] = "failed"
        if last_parsed_path is not None:
            record["result_sha256"] = file_sha256(last_parsed_path)
            record["result_path"] = str(last_parsed_path.relative_to(self.project))
        state["status"] = "running"
        self._save_state(state)
        if self.manifest["executor"].get("failure_policy") == "fail-fast":
            raise ExecutionBlocked(f"trial failed after retries: {trial_id}")
        return last_parsed

    def _aggregate(
        self,
        state: dict[str, Any],
        results: dict[str, dict[str, Any]],
        outcome: str | None = None,
    ) -> dict[str, Any]:
        statuses = {trial_id: record["status"] for trial_id, record in state["trials"].items()}
        if outcome is None:
            all_scientifically_completed = (
                len(results) == len(statuses)
                and all(item["outcome"] == "completed" for item in results.values())
            )
            outcome = "completed" if all_scientifically_completed else "inconclusive"
        aggregate = {
            "schema_version": self.schema_version,
            "execution_id": self.execution_id,
            "outcome": outcome,
            "sessions_started": int(state["sessions_started"]),
            "sessions_completed": sum(int(item["attempts"]) for item in state["trials"].values() if item["status"] in {"completed", "failed"}),
            "cost_usd": float(state["cost_usd"]),
            "wall_seconds": float(state.get("wall_seconds", 0.0)),
            "budget_exceeded": outcome == "terminated",
            "trial_ids": list(statuses),
            "trial_statuses": statuses,
            "result_hashes": {
                trial_id: state["trials"][trial_id]["result_sha256"]
                for trial_id in results
                if state["trials"][trial_id].get("result_sha256")
            },
            "result_files": {
                trial_id: state["trials"][trial_id]["result_path"]
                for trial_id in results
                if state["trials"][trial_id].get("result_path")
            },
            "checkpoint_path": "runs/formal-experiment/executor-state.json",
        }
        atomic_object(self.result_path, aggregate)
        return aggregate

    def execute(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ExecutionBlocked("another LocalExecutor is active") from exc
            state = self._load_state()
            self._recover(state)
            results: dict[str, dict[str, Any]] = {}
            try:
                for trial in self.manifest["trials"]:
                    result = self._run_trial(state, trial)
                    if result is not None:
                        results[trial["trial_id"]] = result
                if float(state["cost_usd"]) > float(self.budget["max_cost_usd"]):
                    raise ExecutionTerminated("post-trial cost budget exceeded")
                if float(state.get("wall_seconds", 0.0)) > float(self.budget["max_wall_seconds"]):
                    raise ExecutionTerminated("post-trial wall-time budget exceeded")
                state["status"] = "completed"
                self._save_state(state)
                return self._aggregate(state, results)
            except ExecutionTerminated:
                state["status"] = "terminated"
                self._save_state(state)
                self._aggregate(state, results, outcome="terminated")
                raise
