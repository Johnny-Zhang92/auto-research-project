#!/usr/bin/env python3
"""Framework-owned authorization guard and executor dispatcher."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from executor import ExecutionBlocked, ExecutionTerminated
from local_executor import LocalExecutor, atomic_object


PROJECT = Path.cwd().resolve()
MANIFEST_PATH = PROJECT / "configs" / "execution-manifest.json"
AUTHORIZATION_PATH = PROJECT / ".research" / "execution-authorization.json"
ENVIRONMENT_LOCK_PATH = PROJECT / ".research" / "environment-lock.json"
FROZEN_MANIFEST_PATH = PROJECT / ".research" / "frozen-manifest.json"
GUARD_REPORT = PROJECT / "runs" / "formal-experiment" / "guard-report.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_project_file(relative: Any) -> Path:
    if (
        not isinstance(relative, str)
        or not relative
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
    ):
        raise ExecutionBlocked(f"unsafe frozen path: {relative!r}")
    path = (PROJECT / relative).resolve()
    try:
        path.relative_to(PROJECT)
    except ValueError as exc:
        raise ExecutionBlocked(f"frozen path escapes project: {relative}") from exc
    if not path.is_file() or path.is_symlink():
        raise ExecutionBlocked(f"frozen file is missing or a symlink: {relative}")
    return path


def validate_frozen_files(authorization: dict[str, Any]) -> None:
    if not FROZEN_MANIFEST_PATH.is_file():
        raise ExecutionBlocked("frozen manifest is missing")
    if authorization.get("frozen_manifest_sha256") != file_sha256(FROZEN_MANIFEST_PATH):
        raise ExecutionBlocked("frozen manifest hash differs from authorization")
    frozen = read_object(FROZEN_MANIFEST_PATH).get("files")
    if not isinstance(frozen, dict) or not frozen:
        raise ExecutionBlocked("frozen manifest has no files")
    for relative, expected in frozen.items():
        if not isinstance(expected, str) or file_sha256(safe_project_file(relative)) != expected:
            raise ExecutionBlocked(f"frozen input hash differs: {relative}")


def validate_environment_lock(
    manifest: dict[str, Any], authorization: dict[str, Any]
) -> None:
    if not ENVIRONMENT_LOCK_PATH.is_file():
        raise ExecutionBlocked("environment lock is missing")
    if authorization.get("environment_lock_sha256") != file_sha256(ENVIRONMENT_LOCK_PATH):
        raise ExecutionBlocked("environment lock hash differs from authorization")
    lock = read_object(ENVIRONMENT_LOCK_PATH)
    if lock.get("schema_version") != manifest.get("schema_version") or lock.get("execution_id") != manifest.get("execution_id"):
        raise ExecutionBlocked("environment lock identity differs")
    if lock.get("executor") != manifest.get("executor"):
        raise ExecutionBlocked("environment lock executor differs")
    actual_runtime = {
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    if lock.get("runtime") != actual_runtime:
        raise ExecutionBlocked("current runtime differs from environment lock")
    if lock.get("pass_env_names") != manifest.get("environment", {}).get("pass_env"):
        raise ExecutionBlocked("approved environment variable names differ")
    files = lock.get("files")
    if not isinstance(files, list):
        raise ExecutionBlocked("environment lock files are invalid")
    for item in files:
        if not isinstance(item, dict) or set(item) != {"role", "path", "sha256"}:
            raise ExecutionBlocked("environment lock file record is invalid")
        if file_sha256(safe_project_file(item["path"])) != item["sha256"]:
            raise ExecutionBlocked(f"environment file hash differs: {item['path']}")


def report(
    status: str,
    reason: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {"status": status, "at": now()}
    if reason:
        payload["reason"] = reason
    if result:
        payload.update(
            execution_id=result.get("execution_id"),
            outcome=result.get("outcome"),
            sessions_started=result.get("sessions_started"),
            cost_usd=result.get("cost_usd"),
            checkpoint_path=result.get("checkpoint_path"),
        )
    atomic_object(GUARD_REPORT, payload)


def validate_authorization(
    manifest: dict[str, Any], authorization: dict[str, Any]
) -> None:
    if manifest.get("schema_version") != authorization.get("schema_version"):
        raise ExecutionBlocked("manifest and authorization schema versions differ")
    if manifest.get("execution_id") != authorization.get("execution_id"):
        raise ExecutionBlocked("manifest and authorization execution IDs differ")
    if manifest.get("budget") != authorization.get("budget"):
        raise ExecutionBlocked("authorization and manifest budgets differ")
    if manifest.get("entrypoint") != ["python3", "scripts/execute-approved.py"]:
        raise ExecutionBlocked("unapproved entrypoint")
    executor = manifest.get("executor")
    if not isinstance(executor, dict) or executor.get("type") != "local":
        raise ExecutionBlocked("only the approved local executor is available")
    if executor.get("protocol_version") != LocalExecutor.protocol_version:
        raise ExecutionBlocked("executor protocol version differs")
    if authorization.get("execution_manifest_sha256") != file_sha256(MANIFEST_PATH):
        raise ExecutionBlocked("execution manifest hash differs from authorization")
    validate_environment_lock(manifest, authorization)
    validate_frozen_files(authorization)


def main() -> int:
    try:
        manifest = read_object(MANIFEST_PATH)
        authorization = read_object(AUTHORIZATION_PATH)
        validate_authorization(manifest, authorization)
        executor = LocalExecutor(
            PROJECT,
            manifest,
            authorization,
            schema_version=str(manifest["schema_version"]),
        )
        result = executor.execute()
        report("completed", result=result)
        return 0
    except ExecutionTerminated as exc:
        result = read_object(PROJECT / "runs" / "formal-experiment" / "result.json")
        report("terminated", str(exc), result)
        print(str(exc), file=sys.stderr)
        return 3
    except (
        ExecutionBlocked,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        report("blocked", str(exc))
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
