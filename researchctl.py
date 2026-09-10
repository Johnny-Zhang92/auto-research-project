#!/usr/bin/env python3
"""Dependency-free state-machine runner for OpenCode Research Mode."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
PROJECTS = ROOT / "projects"
WORKFLOW_PATH = ROOT / "workflow" / "research-workflow.json"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
SKILLS_LOCK_PATH = ROOT / "skills.lock.json"
SCHEMA_REGISTRY_PATH = ROOT / "schemas" / "schema-registry.json"
TERMINALS = {"done", "blocked", "terminated"}
FREEZE_PATHS = ("scripts", "configs", "tasks", ".research/runtime", ".opencode/agents/bench-agent.md")
SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
SAFE_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def project_path(project_id: str) -> Path:
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-"
    if not project_id or any(char not in allowed for char in project_id):
        raise SystemExit("project id must contain only lowercase letters, digits and hyphens")
    return PROJECTS / project_id


def state_path(project: Path) -> Path:
    return project / ".research" / "state.json"


def event(project: Path, kind: str, **payload: object) -> None:
    record = {"at": now(), "event": kind, **payload}
    path = project / ".research" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class ProjectLock:
    """Prevent concurrent runners or approvals for one project."""

    def __init__(self, project: Path) -> None:
        self.path = project / ".research" / "runner.lock"
        self.handle: Any = None

    def __enter__(self) -> "ProjectLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            raise SystemExit(f"another researchctl process holds {self.path}") from exc
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(json.dumps({"pid": os.getpid(), "acquired_at": now()}) + "\n")
        self.handle.flush()
        return self

    def __exit__(self, *_: object) -> None:
        if self.handle:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def render_status(project: Path, state: dict[str, Any]) -> None:
    active = state.get("active_run") or {}
    text = (
        "# Research status\n\n"
        f"- Project: `{state['project_id']}`\n"
        f"- Workflow: `{state['workflow_version']}`\n"
        f"- Current phase: `{state['phase']}`\n"
        f"- Status: `{state['status']}`\n"
        f"- Updated: `{state['updated_at']}`\n"
        f"- Attempts: `{state.get('attempts', 0)}`\n"
        f"- Interruptions: `{state.get('interruptions', 0)}`\n"
        f"- Active run: `{active.get('run_id', 'none')}`\n"
        f"- Blocker: {state.get('blocker') or 'none'}\n\n"
        "Machine state: `.research/state.json`. Event history: `.research/events.jsonl`.\n"
    )
    (project / "STATUS.md").write_text(text, encoding="utf-8")


def save_state(project: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now()
    atomic_json(state_path(project), state)
    render_status(project, state)


def require_keys(data: dict[str, Any], keys: set[str], label: str) -> None:
    missing = sorted(keys - set(data))
    if missing:
        raise ValueError(f"{label} missing keys: {', '.join(missing)}")


def require_nonempty_list(data: dict[str, Any], key: str, label: str, minimum: int = 1) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list) or len(value) < minimum:
        raise ValueError(f"{label}.{key} must contain at least {minimum} item(s)")
    return value


def require_positive_number(data: dict[str, Any], key: str, label: str) -> float:
    value = data.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{label}.{key} must be a positive number")
    return float(value)


def validate_budget(budget: Any, label: str = "budget") -> None:
    if not isinstance(budget, dict):
        raise ValueError(f"{label} must be an object")
    require_positive_number(budget, "max_cost_usd", label)
    require_positive_number(budget, "max_wall_seconds", label)
    require_positive_number(budget, "max_run_seconds", label)
    retries = budget.get("max_retries")
    if not isinstance(retries, int) or isinstance(retries, bool) or not 0 <= retries <= 5:
        raise ValueError(f"{label}.max_retries must be an integer from 0 to 5")
    sessions = budget.get("max_sessions")
    if not isinstance(sessions, int) or isinstance(sessions, bool) or sessions < 1:
        raise ValueError(f"{label}.max_sessions must be a positive integer")


def require_schema_version(data: dict[str, Any], label: str) -> None:
    if data.get("schema_version") != VERSION:
        raise ValueError(f"{label}.schema_version must equal {VERSION}")


def validate_research_contract(data: dict[str, Any], _: Path) -> None:
    require_schema_version(data, "research contract")
    require_keys(data, {"schema_version", "research_question", "scope", "known_facts", "assumptions", "unknowns", "exclusions", "budget", "stop_conditions"}, "research contract")
    if not isinstance(data["research_question"], str) or not data["research_question"].strip():
        raise ValueError("research_contract.research_question must be non-empty")
    for key in ("known_facts", "assumptions", "unknowns", "exclusions", "stop_conditions"):
        require_nonempty_list(data, key, "research contract")
    validate_budget(data["budget"])


def validate_evidence_ledger(data: dict[str, Any], project: Path) -> None:
    problems: list[str] = []
    expected_keys = {"schema_version", "queries", "claims", "evidence", "unresolved"}
    if data.get("schema_version") != VERSION:
        problems.append(f"schema_version must equal {VERSION}")
    if set(data) != expected_keys:
        missing = sorted(expected_keys - set(data))
        extra = sorted(set(data) - expected_keys)
        if missing:
            problems.append("missing top-level keys: " + ", ".join(missing))
        if extra:
            problems.append("unexpected top-level keys: " + ", ".join(extra))
    queries = data.get("queries") if isinstance(data.get("queries"), list) else []
    claims = data.get("claims") if isinstance(data.get("claims"), list) else []
    entries = data.get("evidence") if isinstance(data.get("evidence"), list) else []
    if not queries:
        problems.append("queries must contain at least one item")
    if not claims:
        problems.append("claims must contain at least one item")
    if not entries:
        problems.append("evidence must contain at least one item")
    if not isinstance(data.get("unresolved"), list):
        problems.append("unresolved must be an array")
    claim_ids: set[str] = set()
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            problems.append(f"claims[{index}] must be an object")
            continue
        if set(claim) != {"id", "text"}:
            problems.append(f"claims[{index}] keys must equal id,text")
        claim_id = claim.get("id")
        text = claim.get("text")
        if not isinstance(claim_id, str) or not claim_id.strip():
            problems.append(f"claims[{index}].id must be non-empty")
        elif claim_id in claim_ids:
            problems.append(f"duplicate claim id: {claim_id}")
        else:
            claim_ids.add(claim_id)
        if not isinstance(text, str) or not text.strip():
            problems.append(f"claims[{index}].text must be non-empty")
    query_ids: set[str] = set()
    for index, query in enumerate(queries):
        if not isinstance(query, dict):
            problems.append(f"queries[{index}] must be an object")
            continue
        query_keys = {"id", "backend", "endpoint", "parameters", "executed_at", "result_count", "status", "snapshot"}
        if set(query) != query_keys:
            problems.append(f"queries[{index}] keys do not match the contract")
        if query.get("status") not in {"completed", "partial", "failed"}:
            problems.append(f"queries[{index}].status={query.get('status')!r} is invalid; allowed completed,partial,failed")
        if not isinstance(query.get("parameters"), dict):
            problems.append(f"queries[{index}].parameters must be an object")
        count = query.get("result_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            problems.append(f"queries[{index}].result_count must be a non-negative integer")
        for field in ("backend", "endpoint", "executed_at"):
            if not isinstance(query.get(field), str) or not query[field].strip():
                problems.append(f"queries[{index}].{field} must be non-empty")
        query_id = query.get("id")
        if not isinstance(query_id, str) or not query_id or query_id in query_ids:
            problems.append(f"queries[{index}].id must be unique and non-empty")
        else:
            query_ids.add(query_id)
        snapshot = query.get("snapshot")
        if not isinstance(snapshot, dict) or set(snapshot) != {"path", "sha256"}:
            problems.append(f"queries[{index}].snapshot must contain exactly path and sha256")
        else:
            try:
                snapshot_path = safe_project_file(project, snapshot.get("path"), prefix="research/evidence/snapshots/")
                if file_sha256(snapshot_path) != snapshot.get("sha256"):
                    problems.append(f"queries[{index}] snapshot hash mismatch")
            except (OSError, ValueError, TypeError) as exc:
                problems.append(f"queries[{index}] snapshot invalid: {exc}")
    allowed_stances = {"supports", "contradicts", "neutral", "mixed"}
    allowed_verification = {"verified", "metadata-only", "unresolved", "blocked"}
    evidence_ids: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            problems.append(f"evidence[{index}] must be an object")
            continue
        entry_keys = {"id", "claim_ids", "source", "retrieval_query_id", "accessed_at", "stance", "verification_status"}
        if set(entry) != entry_keys:
            problems.append(f"evidence[{index}] keys do not match the contract")
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id or entry_id in evidence_ids:
            problems.append(f"evidence[{index}].id must be unique and non-empty")
        else:
            evidence_ids.add(entry_id)
        linked_claims = entry.get("claim_ids")
        if not isinstance(linked_claims, list) or not linked_claims:
            problems.append(f"evidence[{index}].claim_ids must contain at least one item")
        elif not set(linked_claims) <= claim_ids:
            problems.append(f"evidence[{index}] references unknown claim ids")
        source = entry.get("source")
        if not isinstance(source, dict):
            problems.append(f"evidence[{index}].source must be an object")
        else:
            if set(source) != {"url", "persistent_id", "title"}:
                problems.append(f"evidence[{index}].source keys must equal url,persistent_id,title")
            if not isinstance(source.get("url"), str) or not source["url"].startswith(("https://", "http://", "local:")):
                problems.append(f"evidence[{index}].source.url must be http(s) or local:")
            for field in ("persistent_id", "title"):
                if not isinstance(source.get(field), str) or not source[field].strip():
                    problems.append(f"evidence[{index}].source.{field} is required")
        if entry.get("retrieval_query_id") not in query_ids:
            problems.append(f"evidence[{index}] references an unknown query")
        if not isinstance(entry.get("accessed_at"), str) or not entry["accessed_at"].strip():
            problems.append(f"evidence[{index}].accessed_at must be non-empty")
        if entry.get("stance") not in allowed_stances:
            problems.append(f"evidence[{index}].stance={entry.get('stance')!r} is invalid; allowed contradicts,mixed,neutral,supports")
        if entry.get("verification_status") not in allowed_verification:
            problems.append(f"evidence[{index}].verification_status={entry.get('verification_status')!r} is invalid; allowed blocked,metadata-only,unresolved,verified")
    if problems:
        raise ValueError(" | ".join(problems[:50]))


def hypothesis_validation_issues(data: dict[str, Any], project: Path) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    expected = {"schema_version", "research_question", "hypotheses", "measurements"}
    if data.get("schema_version") != VERSION:
        issues.append(("schema", f"schema_version must equal {VERSION}"))
    if set(data) != expected:
        missing = sorted(expected - set(data))
        extra = sorted(set(data) - expected)
        if missing:
            issues.append(("shape", "missing top-level keys: " + ", ".join(missing)))
        if extra:
            issues.append(("shape", "unexpected top-level keys: " + ", ".join(extra)))
    if not isinstance(data.get("research_question"), str) or not data.get("research_question", "").strip():
        issues.append(("content", "research_question must be non-empty"))
    measurements = data.get("measurements")
    if not isinstance(measurements, list) or not measurements:
        issues.append(("content", "measurements must contain at least 1 item(s)"))
    elif any(not isinstance(item, str) or not item.strip() for item in measurements):
        issues.append(("content", "measurements must contain only non-empty strings"))

    ledger = load_json(project / "research" / "evidence-ledger.json")
    evidence_ids = {item.get("id") for item in ledger.get("evidence", []) if isinstance(item, dict)}
    claim_ids = {item.get("id") for item in ledger.get("claims", []) if isinstance(item, dict)}
    hypotheses = data.get("hypotheses")
    if not isinstance(hypotheses, list) or not hypotheses:
        return issues + [("content", "hypotheses must contain at least 1 item(s)")]
    seen_ids: set[str] = set()
    required = {"id", "null", "alternative", "predictions", "rivals", "falsifier", "evidence_ids"}
    for index, item in enumerate(hypotheses):
        label = f"hypotheses[{index}]"
        if not isinstance(item, dict):
            issues.append(("shape", f"{label} must be an object"))
            continue
        missing = sorted(required - set(item))
        extra = sorted(set(item) - required)
        if missing:
            issues.append(("shape", f"{label} missing keys: {', '.join(missing)}"))
        if extra:
            issues.append(("shape", f"{label} unexpected keys: {', '.join(extra)}"))
        hypothesis_id = item.get("id")
        if not isinstance(hypothesis_id, str) or not hypothesis_id.strip():
            issues.append(("identity", f"{label}.id must be non-empty"))
        elif hypothesis_id in seen_ids:
            issues.append(("identity", f"{label}.id must be unique"))
        else:
            seen_ids.add(hypothesis_id)
        for key in ("null", "alternative", "falsifier"):
            if not isinstance(item.get(key), str) or not item.get(key, "").strip():
                issues.append(("content", f"{label}.{key} must be non-empty"))
        for key in ("predictions", "rivals"):
            value = item.get(key)
            if not isinstance(value, list) or not value:
                issues.append(("content", f"{label}.{key} must contain at least 1 item(s)"))
            elif any(not isinstance(entry, str) or not entry.strip() for entry in value):
                issues.append(("content", f"{label}.{key} must contain only non-empty strings"))
        linked = item.get("evidence_ids")
        if not isinstance(linked, list) or not linked:
            issues.append(("unsupported", f"{label}.evidence_ids must contain at least 1 item(s)"))
            continue
        if any(not isinstance(entry, str) or not entry.strip() for entry in linked):
            issues.append(("reference", f"{label}.evidence_ids must contain only non-empty strings"))
            continue
        claim_refs = sorted(set(linked) & claim_ids)
        unknown_refs = sorted(set(linked) - evidence_ids - claim_ids)
        if claim_refs:
            issues.append(("claim_reference", f"{label}.evidence_ids contains claim IDs, not evidence IDs: {', '.join(claim_refs)}"))
        if unknown_refs:
            issues.append(("unknown_reference", f"{label}.evidence_ids references unknown IDs: {', '.join(unknown_refs)}"))
        if len(set(linked)) != len(linked):
            issues.append(("reference", f"{label}.evidence_ids must be unique"))
    return issues


def validate_hypothesis_ledger(data: dict[str, Any], project: Path) -> None:
    issues = hypothesis_validation_issues(data, project)
    if issues:
        raise ValueError(" | ".join(message for _, message in issues[:50]))


def validate_experiment_plan(data: dict[str, Any], project: Path) -> None:
    problems: list[str] = []
    expected_keys = {"schema_version", "hypothesis_ids", "conditions", "tasks", "metrics", "repetitions", "design", "budget", "stop_conditions", "analysis"}
    if data.get("schema_version") != VERSION:
        problems.append(f"schema_version must equal {VERSION}")
    if set(data) != expected_keys:
        missing = sorted(expected_keys - set(data))
        extra = sorted(set(data) - expected_keys)
        if missing:
            problems.append("missing top-level keys: " + ", ".join(missing))
        if extra:
            problems.append("unexpected top-level keys: " + ", ".join(extra))

    known_hypotheses = {
        item["id"] for item in load_json(project / "research" / "hypothesis-ledger.json").get("hypotheses", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    hypothesis_ids = data.get("hypothesis_ids")
    if not isinstance(hypothesis_ids, list) or not hypothesis_ids:
        problems.append("hypothesis_ids must contain at least one item")
    else:
        if any(not isinstance(item, str) or not item.strip() for item in hypothesis_ids):
            problems.append("hypothesis_ids must contain only non-empty strings")
        if len(set(item for item in hypothesis_ids if isinstance(item, str))) != len(hypothesis_ids):
            problems.append("hypothesis_ids must be unique")
        unknown = sorted(set(item for item in hypothesis_ids if isinstance(item, str)) - known_hypotheses)
        if unknown:
            problems.append("unknown hypothesis ids: " + ", ".join(unknown))

    conditions = data.get("conditions")
    condition_ids: list[str] = []
    if not isinstance(conditions, list) or len(conditions) < 2:
        problems.append("conditions must contain at least two items")
    else:
        for index, condition in enumerate(conditions):
            condition_id = condition if isinstance(condition, str) else condition.get("id") if isinstance(condition, dict) else None
            if not isinstance(condition_id, str) or not condition_id.strip():
                problems.append(f"conditions[{index}] must be a non-empty string or object with a non-empty id")
            else:
                condition_ids.append(condition_id)
        if len(set(condition_ids)) != len(condition_ids):
            problems.append("condition ids must be unique")

    tasks = data.get("tasks")
    categories: set[str] = set()
    task_ids: list[str] = []
    if not isinstance(tasks, list) or len(tasks) < 4:
        problems.append("tasks must contain at least four items")
        tasks = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            problems.append(f"tasks[{index}] must be an object")
            continue
        missing = sorted({"id", "category", "deterministic_grader", "hidden_tests"} - set(task))
        if missing:
            problems.append(f"tasks[{index}] missing keys: {', '.join(missing)}")
        task_id = task.get("id")
        category = task.get("category")
        if not isinstance(task_id, str) or not task_id.strip():
            problems.append(f"tasks[{index}].id must be non-empty")
        else:
            task_ids.append(task_id)
        if not isinstance(category, str) or not category.strip():
            problems.append(f"tasks[{index}].category must be non-empty")
        else:
            categories.add(category)
        if task.get("deterministic_grader") is not True:
            problems.append(f"tasks[{index}].deterministic_grader must be true")
        if task.get("hidden_tests") is not True:
            problems.append(f"tasks[{index}].hidden_tests must be true")
    if len(set(task_ids)) != len(task_ids):
        problems.append("task ids must be unique")
    if len(categories) < 3:
        problems.append("tasks must cover at least three categories")

    metrics = data.get("metrics")
    metric_names: list[str] = []
    primary_names: list[str] = []
    if not isinstance(metrics, list) or not metrics:
        problems.append("metrics must contain at least one item")
        metrics = []
    for index, metric in enumerate(metrics):
        if not isinstance(metric, dict):
            problems.append(f"metrics[{index}] must be an object")
            continue
        name = metric.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"metrics[{index}].name must be non-empty")
        else:
            metric_names.append(name)
            if metric.get("primary") is True:
                primary_names.append(name)
        if not isinstance(metric.get("primary"), bool):
            problems.append(f"metrics[{index}].primary must be boolean")
    if len(set(metric_names)) != len(metric_names):
        problems.append("metric names must be unique")
    if len(primary_names) != 1:
        problems.append("metrics must declare exactly one primary metric")

    repetitions = data.get("repetitions")
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 1:
        problems.append("repetitions must be a positive integer")

    budget = data.get("budget")
    if not isinstance(budget, dict):
        problems.append("budget must be an object")
    else:
        for key in ("max_cost_usd", "max_wall_seconds", "max_run_seconds"):
            value = budget.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                problems.append(f"budget.{key} must be a positive number")
        retries = budget.get("max_retries")
        if not isinstance(retries, int) or isinstance(retries, bool) or not 0 <= retries <= 5:
            problems.append("budget.max_retries must be an integer from 0 to 5")
        sessions = budget.get("max_sessions")
        if not isinstance(sessions, int) or isinstance(sessions, bool) or sessions < 1:
            problems.append("budget.max_sessions must be a positive integer")
        elif tasks and isinstance(conditions, list) and isinstance(repetitions, int) and not isinstance(repetitions, bool) and repetitions > 0:
            planned_trials = len(tasks) * len(conditions) * repetitions
            if planned_trials > sessions:
                problems.append(f"planned trials {planned_trials} exceed budget.max_sessions {sessions}")
        try:
            contract_budget = load_json(project / "research" / "research-contract.json")["budget"]
            for key in ("max_cost_usd", "max_wall_seconds", "max_run_seconds", "max_retries", "max_sessions"):
                if isinstance(budget.get(key), (int, float)) and not isinstance(budget.get(key), bool) and budget[key] > contract_budget[key]:
                    problems.append(f"budget.{key} exceeds the research contract")
        except (KeyError, OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
            problems.append(f"research contract budget is invalid: {exc}")

    stop_conditions = data.get("stop_conditions")
    if not isinstance(stop_conditions, list) or not stop_conditions:
        problems.append("stop_conditions must contain at least one item")
    elif any(not isinstance(item, str) or not item.strip() for item in stop_conditions):
        problems.append("stop_conditions must contain only non-empty strings")

    design = data.get("design")
    design_keys = {"unit_of_analysis", "replication_unit", "assignment", "randomization", "blocking", "seeds", "data_split", "leakage_controls"}
    if not isinstance(design, dict):
        problems.append("design must be an object")
    else:
        missing = sorted(design_keys - set(design))
        if missing:
            problems.append("design missing keys: " + ", ".join(missing))
        for key in ("unit_of_analysis", "replication_unit", "assignment", "randomization", "blocking", "data_split"):
            if not isinstance(design.get(key), str) or not design.get(key, "").strip():
                problems.append(f"design.{key} must be non-empty")
        for key in ("seeds", "leakage_controls"):
            if not isinstance(design.get(key), list) or not design.get(key):
                problems.append(f"design.{key} must contain at least one item")

    analysis = data.get("analysis")
    analysis_keys = {"primary_metric", "multiplicity", "missing_data", "sensitivity"}
    if not isinstance(analysis, dict):
        problems.append("analysis must be an object")
    else:
        missing = sorted(analysis_keys - set(analysis))
        if missing:
            problems.append("analysis missing keys: " + ", ".join(missing))
        for key in analysis_keys:
            if not isinstance(analysis.get(key), str) or not analysis.get(key, "").strip():
                problems.append(f"analysis.{key} must be non-empty")
        if metric_names and analysis.get("primary_metric") not in metric_names:
            problems.append("analysis.primary_metric must name a declared metric")
        if primary_names and analysis.get("primary_metric") != primary_names[0]:
            problems.append("analysis.primary_metric must match the primary metric")

    if problems:
        raise ValueError(" | ".join(problems[:50]))


def validate_hashed_files(items: Any, project: Path, label: str, minimum: int = 0) -> list[dict[str, str]]:
    if not isinstance(items, list) or len(items) < minimum:
        raise ValueError(f"{label} must contain at least {minimum} item(s)")
    seen: set[str] = set()
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError(f"{label}[{index}] must contain exactly path and sha256")
        path = safe_project_file(project, item["path"])
        if path.is_symlink():
            raise ValueError(f"{label}[{index}] must not be a symlink")
        if item["path"] in seen:
            raise ValueError(f"{label} contains duplicate path: {item['path']}")
        if file_sha256(path) != item["sha256"]:
            raise ValueError(f"{label}[{index}] hash mismatch: {item['path']}")
        seen.add(item["path"])
        normalized.append(item)
    return normalized


def validate_implementation(data: dict[str, Any], project: Path) -> None:
    require_schema_version(data, "execution manifest")
    manifest_keys = {"schema_version", "execution_id", "executor", "entrypoint", "trials", "estimated_sessions", "budget", "frozen_inputs", "environment"}
    if set(data) != manifest_keys:
        raise ValueError("execution manifest keys do not match the protocol")
    if not isinstance(data["execution_id"], str) or not SAFE_IDENTIFIER.fullmatch(data["execution_id"]):
        raise ValueError("execution manifest.execution_id is invalid")
    executor = data["executor"]
    if not isinstance(executor, dict) or set(executor) != {"type", "protocol_version", "max_parallel", "failure_policy"}:
        raise ValueError("execution manifest.executor has invalid fields")
    if executor.get("type") != "local" or executor.get("protocol_version") != "1" or executor.get("max_parallel") != 1:
        raise ValueError("only local executor protocol 1 with max_parallel=1 is supported")
    if executor["failure_policy"] not in {"continue", "fail-fast"}:
        raise ValueError("execution manifest.executor.failure_policy is invalid")
    if data["entrypoint"] != ["python3", "scripts/execute-approved.py"]:
        raise ValueError("entrypoint must be the exact argv array ['python3', 'scripts/execute-approved.py']")
    safe_project_file(project, "scripts/execute-approved.py")
    sessions = data["estimated_sessions"]
    if not isinstance(sessions, int) or sessions < 1:
        raise ValueError("estimated_sessions must be a positive integer")
    validate_budget(data["budget"])
    trials = require_nonempty_list(data, "trials", "execution manifest")
    if len(trials) != sessions:
        raise ValueError("estimated_sessions must equal the number of trials")
    if sessions > data["budget"]["max_sessions"]:
        raise ValueError("planned trials exceed max_sessions before retries")
    plan = load_json(project / "research" / "experiment-plan.json")
    known_tasks = {item["id"] for item in plan["tasks"]}
    known_conditions = {
        condition if isinstance(condition, str) else condition["id"]
        for condition in plan["conditions"]
    }
    trial_ids: set[str] = set()
    for index, trial in enumerate(trials):
        if not isinstance(trial, dict):
            raise ValueError(f"trials[{index}] must be an object")
        if set(trial) != {"trial_id", "condition", "task_id", "seed", "parameters"}:
            raise ValueError(f"trials[{index}] keys do not match the protocol")
        trial_id = trial["trial_id"]
        if not isinstance(trial_id, str) or not SAFE_IDENTIFIER.fullmatch(trial_id) or trial_id in trial_ids:
            raise ValueError(f"trials[{index}].trial_id must be unique and valid")
        trial_ids.add(trial_id)
        if trial["task_id"] not in known_tasks or trial["condition"] not in known_conditions:
            raise ValueError(f"trials[{index}] references an unknown task or condition")
        if not isinstance(trial["seed"], int) or isinstance(trial["seed"], bool):
            raise ValueError(f"trials[{index}].seed must be an integer")
        if not isinstance(trial["parameters"], dict):
            raise ValueError(f"trials[{index}].parameters must be an object")
    require_nonempty_list(data, "frozen_inputs", "execution manifest")
    environment = data["environment"]
    if not isinstance(environment, dict) or set(environment) != {"python", "dependency_lock", "code_snapshot", "data_artifacts", "pass_env"}:
        raise ValueError("execution manifest.environment has invalid fields")
    python = environment["python"]
    if not isinstance(python, dict) or set(python) != {"version", "executable"} or python["executable"] != "python3":
        raise ValueError("execution manifest.environment.python is invalid")
    if not isinstance(python["version"], str) or not python["version"].strip():
        raise ValueError("execution manifest.environment.python.version is required")
    dependency = environment["dependency_lock"]
    if not isinstance(dependency, dict) or set(dependency) != {"path", "sha256", "reason"}:
        raise ValueError("execution manifest.environment.dependency_lock is invalid")
    if dependency["path"] is None:
        if dependency["sha256"] is not None or not isinstance(dependency["reason"], str) or not dependency["reason"].strip():
            raise ValueError("missing dependency lock requires a reason and null hash")
    else:
        validate_hashed_files([{"path": dependency["path"], "sha256": dependency["sha256"]}], project, "dependency_lock", 1)
    code = validate_hashed_files(environment["code_snapshot"], project, "code_snapshot", 1)
    if "scripts/execute-approved.py" not in {item["path"] for item in code}:
        raise ValueError("code_snapshot must include scripts/execute-approved.py")
    validate_hashed_files(environment["data_artifacts"], project, "data_artifacts")
    pass_env = environment["pass_env"]
    protected_env = {"PATH", "LANG", "LC_ALL", "TZ", "HOME"}
    if (
        not isinstance(pass_env, list)
        or len(pass_env) != len(set(pass_env))
        or not all(isinstance(item, str) and SAFE_ENV_NAME.fullmatch(item) for item in pass_env)
        or any(item in protected_env or item.startswith("RESEARCH_") for item in pass_env)
    ):
        raise ValueError("execution manifest.environment.pass_env must contain unique names")


def validate_smoke_result(data: dict[str, Any], _: Path) -> None:
    require_schema_version(data, "smoke result")
    require_keys(data, {"schema_version", "passed", "checks", "measured_sessions_started", "projected_cost_usd", "projected_wall_seconds"}, "smoke result")
    if data["passed"] is not True:
        raise ValueError("smoke result did not pass")
    if data["measured_sessions_started"] != 0:
        raise ValueError("smoke test must not start measured sessions")
    require_nonempty_list(data, "checks", "smoke result")
    if not isinstance(data["projected_cost_usd"], (int, float)) or data["projected_cost_usd"] < 0:
        raise ValueError("projected_cost_usd must be non-negative")
    if not isinstance(data["projected_wall_seconds"], (int, float)) or data["projected_wall_seconds"] < 0:
        raise ValueError("projected_wall_seconds must be non-negative")


def validate_formal_result(data: dict[str, Any], project: Path) -> None:
    require_schema_version(data, "formal result")
    require_keys(data, {"schema_version", "execution_id", "outcome", "sessions_started", "sessions_completed", "cost_usd", "wall_seconds", "budget_exceeded", "trial_ids", "trial_statuses", "result_hashes", "result_files", "checkpoint_path"}, "formal result")
    if data["outcome"] not in {"completed", "inconclusive", "blocked", "terminated"}:
        raise ValueError("formal result.outcome is invalid")
    for key in ("sessions_started", "sessions_completed"):
        if not isinstance(data[key], int) or data[key] < 0:
            raise ValueError(f"formal result.{key} must be a non-negative integer")
    for key in ("cost_usd", "wall_seconds"):
        if not isinstance(data[key], (int, float)) or data[key] < 0:
            raise ValueError(f"formal result.{key} must be non-negative")
    require_nonempty_list(data, "trial_ids", "formal result")
    manifest = load_json(project / "configs" / "execution-manifest.json")
    manifest_trials = [item["trial_id"] for item in manifest["trials"]]
    if data["execution_id"] != manifest["execution_id"]:
        raise ValueError("formal result.execution_id differs from the manifest")
    if data["trial_ids"] != manifest_trials:
        raise ValueError("formal result.trial_ids differ from the manifest")
    if not isinstance(data["trial_statuses"], dict) or set(data["trial_statuses"]) != set(data["trial_ids"]):
        raise ValueError("formal result.trial_statuses must cover every trial")
    if not set(data["trial_statuses"].values()) <= {"pending", "running", "completed", "failed"}:
        raise ValueError("formal result.trial_statuses contains an invalid status")
    if not isinstance(data["result_hashes"], dict) or not set(data["result_hashes"]) <= set(data["trial_ids"]):
        raise ValueError("formal result.result_hashes is invalid")
    if not isinstance(data["result_files"], dict) or set(data["result_files"]) != set(data["result_hashes"]):
        raise ValueError("formal result.result_files must match result_hashes")
    for trial_id, relative in data["result_files"].items():
        result_path = safe_project_file(project, relative, prefix=f"runs/formal-experiment/trials/{trial_id}/")
        if result_path.is_symlink() or file_sha256(result_path) != data["result_hashes"][trial_id]:
            raise ValueError(f"formal result hash mismatch: {trial_id}")
    checkpoint = safe_project_file(project, data["checkpoint_path"], prefix="runs/formal-experiment/")
    if checkpoint.is_symlink():
        raise ValueError("formal result checkpoint must not be a symlink")
    state = load_json(checkpoint)
    if state.get("execution_id") != data["execution_id"]:
        raise ValueError("formal result checkpoint belongs to another execution")
    checkpoint_statuses = {trial_id: item.get("status") for trial_id, item in state.get("trials", {}).items()}
    if checkpoint_statuses != data["trial_statuses"]:
        raise ValueError("formal result trial statuses differ from checkpoint")


def validate_analysis_result(data: dict[str, Any], project: Path) -> None:
    require_schema_version(data, "analysis result")
    require_keys(data, {"schema_version", "hypothesis_ids", "primary_metric", "effect_estimate", "uncertainty", "task_coverage", "limitations", "deviations"}, "analysis result")
    require_nonempty_list(data, "limitations", "analysis result")
    hypothesis_ids = {item["id"] for item in load_json(project / "research" / "hypothesis-ledger.json")["hypotheses"]}
    if not set(require_nonempty_list(data, "hypothesis_ids", "analysis result")) <= hypothesis_ids:
        raise ValueError("analysis result references unknown hypothesis ids")
    if not isinstance(data["deviations"], list):
        raise ValueError("analysis result.deviations must be an array")


def validate_decision(data: dict[str, Any], project: Path) -> None:
    require_schema_version(data, "decision")
    require_keys(data, {"schema_version", "outcome", "requested_next", "claim_ids", "hypothesis_ids", "trial_ids", "reason"}, "decision")
    if data["outcome"] not in {"SUPPORTED", "REFUTED", "INCONCLUSIVE", "BLOCKED", "TERMINATED"}:
        raise ValueError("decision.outcome is invalid")
    if data["requested_next"] not in {"hypotheses", "report", "blocked", "terminated"}:
        raise ValueError("decision.requested_next is invalid")
    claims = {item["id"] for item in load_json(project / "research" / "evidence-ledger.json")["claims"]}
    hypotheses = {item["id"] for item in load_json(project / "research" / "hypothesis-ledger.json")["hypotheses"]}
    trials = set(load_json(project / "runs" / "formal-experiment" / "result.json")["trial_ids"])
    if not set(require_nonempty_list(data, "claim_ids", "decision")) <= claims:
        raise ValueError("decision references unknown claim ids")
    if not set(require_nonempty_list(data, "hypothesis_ids", "decision")) <= hypotheses:
        raise ValueError("decision references unknown hypothesis ids")
    if not set(require_nonempty_list(data, "trial_ids", "decision")) <= trials:
        raise ValueError("decision references unknown trial ids")


VALIDATORS: dict[str, tuple[str, Callable[[dict[str, Any], Path], None]]] = {
    "research_contract": ("research/research-contract.json", validate_research_contract),
    "evidence_ledger": ("research/evidence-ledger.json", validate_evidence_ledger),
    "hypothesis_ledger": ("research/hypothesis-ledger.json", validate_hypothesis_ledger),
    "experiment_plan": ("research/experiment-plan.json", validate_experiment_plan),
    "implementation": ("configs/execution-manifest.json", validate_implementation),
    "smoke_result": ("runs/smoke-test/result.json", validate_smoke_result),
    "formal_result": ("runs/formal-experiment/result.json", validate_formal_result),
    "analysis_result": ("research/result-analysis.json", validate_analysis_result),
    "decision": ("research/decision.json", validate_decision),
}


def semantic_validate(project: Path, validator_name: str | None) -> None:
    if not validator_name:
        return
    relative, validator = VALIDATORS[validator_name]
    validator(load_json(project / relative), project)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_project_file(project: Path, relative: Any, prefix: str | None = None) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError(f"unsafe project-relative path: {relative!r}")
    if prefix and not relative.startswith(prefix):
        raise ValueError(f"path must start with {prefix}: {relative}")
    target = (project / relative).resolve()
    try:
        target.relative_to(project.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes project: {relative}") from exc
    if not target.is_file():
        raise ValueError(f"project file is missing: {relative}")
    return target


def tree_sha256(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in directory.rglob("*") if candidate.is_file() and "__pycache__" not in candidate.parts):
        digest.update(str(path.relative_to(directory)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def skill_lock_entries() -> dict[str, dict[str, Any]]:
    lock = load_json(SKILLS_LOCK_PATH)
    if lock.get("lock_version") != 1 or not isinstance(lock.get("skills"), list):
        raise ValueError("skills.lock.json has an unsupported format")
    entries: dict[str, dict[str, Any]] = {}
    for item in lock["skills"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or item["name"] in entries:
            raise ValueError("skills.lock.json contains an invalid or duplicate entry")
        if item.get("status") not in {"enabled", "reviewed-pending-package", "disabled"}:
            raise ValueError(f"invalid skill status for {item['name']}")
        entries[item["name"]] = item
    return entries


def skill_lock_problems() -> list[str]:
    try:
        entries = skill_lock_entries()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]
    problems: list[str] = []
    for name, item in entries.items():
        if item["status"] != "enabled":
            continue
        directory = ROOT / ".agents" / "skills" / name
        if not (directory / "SKILL.md").is_file():
            problems.append(f"enabled skill is missing: {name}")
        elif tree_sha256(directory) != item.get("tree_sha256"):
            problems.append(f"enabled skill hash mismatch: {name}")
    return problems


def skill_snapshot(names: list[str]) -> list[dict[str, Any]]:
    entries = skill_lock_entries()
    snapshot: list[dict[str, Any]] = []
    for name in names:
        item = entries.get(name)
        if item:
            snapshot.append({key: item.get(key) for key in ("name", "version", "status", "tree_sha256")})
        else:
            snapshot.append({"name": name, "version": None, "status": "external-unlocked", "tree_sha256": None})
    return snapshot


def provenance_path(project: Path, artifact: str) -> Path:
    return project / ".research" / "provenance" / f"{artifact}.provenance.json"


def declared_input_snapshot(project: Path, phase: dict[str, Any]) -> list[dict[str, str]]:
    snapshot: list[dict[str, str]] = []
    controller_owned = ("RESEARCH_GOAL.md", ".research/approvals/", ".research/frozen-manifest.json", ".research/execution-authorization.json", ".research/environment-lock.json")
    for relative in phase.get("inputs", []):
        path = safe_project_file(project, relative)
        sidecar = provenance_path(project, relative)
        requires_sidecar = relative != controller_owned[0] and not any(relative.startswith(prefix) for prefix in controller_owned[1:2]) and relative not in controller_owned[2:]
        if requires_sidecar and not sidecar.is_file():
            raise ValueError(f"input provenance is missing: {relative}")
        if sidecar.is_file():
            record = load_json(sidecar)
            validate_provenance_record(project, record)
            if record.get("artifact") != relative:
                raise ValueError(f"input provenance names another artifact: {relative}")
        snapshot.append({"path": relative, "sha256": file_sha256(path)})
    return snapshot


def write_provenance(
    project: Path,
    phase_name: str,
    phase: dict[str, Any],
    state: dict[str, Any],
    run_id: str,
    prompt: str,
    artifacts: list[Any],
    inputs: list[dict[str, str]],
    generator_agent: str | None = None,
    generator_skills: list[str] | None = None,
) -> None:
    for relative in artifacts:
        path = safe_project_file(project, relative)
        record = {
            "schema_version": VERSION,
            "artifact": relative,
            "artifact_sha256": file_sha256(path),
            "phase": phase_name,
            "run_id": run_id,
            "created_at": now(),
            "generator": {
                "agent": generator_agent or phase.get("agent", "research-worker"),
                "model": state.get("model"),
                "workflow_version": VERSION,
            },
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "skills": skill_snapshot(phase.get("skills", []) if generator_skills is None else generator_skills),
            "inputs": inputs,
        }
        atomic_json(provenance_path(project, relative), record)


def validate_provenance_record(project: Path, record: dict[str, Any]) -> None:
    require_schema_version(record, "provenance")
    require_keys(record, {"schema_version", "artifact", "artifact_sha256", "phase", "run_id", "created_at", "generator", "prompt_sha256", "skills", "inputs"}, "provenance")
    artifact = safe_project_file(project, record["artifact"])
    if file_sha256(artifact) != record["artifact_sha256"]:
        raise ValueError(f"artifact hash mismatch: {record['artifact']}")
    for item in record["inputs"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError(f"invalid provenance input for {record['artifact']}")
        current = safe_project_file(project, item["path"])
        if file_sha256(current) != item["sha256"]:
            raise ValueError(f"upstream input changed: {item['path']} -> {record['artifact']}")


def build_freeze_manifest(project: Path) -> dict[str, Any]:
    files: dict[str, str] = {}
    for relative in FREEZE_PATHS:
        target = project / relative
        candidates = [target] if target.is_file() else sorted(target.rglob("*")) if target.is_dir() else []
        for path in candidates:
            if path.is_file() and path.name != "freeze-manifest.json" and "__pycache__" not in path.parts:
                files[str(path.relative_to(project))] = file_sha256(path)
    if not files:
        raise ValueError("no executable inputs found to freeze")
    return {"created_at": now(), "algorithm": "sha256", "files": files}


def build_environment_lock(project: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Capture controller-observed runtime and verify declared file lineage."""
    validate_implementation(manifest, project)
    declared = manifest["environment"]
    requested_python = declared["python"]["version"]
    actual_python = platform.python_version()
    if not actual_python.startswith(requested_python):
        raise ValueError(
            f"Python runtime differs: requested {requested_python}, actual {actual_python}"
        )
    files: list[dict[str, str]] = []
    dependency = declared["dependency_lock"]
    if dependency["path"] is not None:
        files.append({"role": "dependency_lock", "path": dependency["path"], "sha256": dependency["sha256"]})
    files.extend({"role": "code", **item} for item in declared["code_snapshot"])
    files.extend({"role": "data", **item} for item in declared["data_artifacts"])
    return {
        "schema_version": VERSION,
        "execution_id": manifest["execution_id"],
        "captured_at": now(),
        "executor": manifest["executor"],
        "runtime": {
            "python_executable": sys.executable,
            "python_version": actual_python,
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "declared_python": declared["python"],
        "dependency_lock_reason": dependency["reason"],
        "pass_env_names": declared["pass_env"],
        "files": files,
    }


def verify_freeze(project: Path) -> tuple[bool, str]:
    manifest_path = project / ".research" / "frozen-manifest.json"
    if not manifest_path.is_file():
        return False, "frozen manifest is missing"
    expected = load_json(manifest_path).get("files")
    if not isinstance(expected, dict) or not expected:
        return False, "frozen manifest has no files"
    current = build_freeze_manifest(project)["files"]
    if current != expected:
        changed = sorted(set(current) ^ set(expected) | {key for key in set(current) & set(expected) if current[key] != expected[key]})
        return False, "frozen inputs changed: " + ", ".join(changed[:20])
    return True, "ok"


def verify_execution_authorization(project: Path) -> tuple[bool, str]:
    try:
        authorization_path = project / ".research" / "execution-authorization.json"
        manifest_path = project / "configs" / "execution-manifest.json"
        frozen_path = project / ".research" / "frozen-manifest.json"
        environment_path = project / ".research" / "environment-lock.json"
        authorization = load_json(authorization_path)
        manifest = load_json(manifest_path)
        environment = load_json(environment_path)
        if authorization.get("schema_version") != VERSION:
            return False, "execution authorization schema version differs"
        if authorization.get("execution_id") != manifest.get("execution_id") or environment.get("execution_id") != manifest.get("execution_id"):
            return False, "execution authorization identity differs"
        if authorization.get("budget") != manifest.get("budget"):
            return False, "execution authorization budget differs"
        expected = {
            "execution_manifest_sha256": file_sha256(manifest_path),
            "frozen_manifest_sha256": file_sha256(frozen_path),
            "environment_lock_sha256": file_sha256(environment_path),
        }
        for key, actual in expected.items():
            if authorization.get(key) != actual:
                return False, f"{key} differs from execution authorization"
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, f"invalid execution authorization: {exc}"
    return True, "ok"


def phase_json_contract(validator_name: str | None) -> str:
    contracts = {
        "research_contract": f"schema_version={VERSION}; research_question, scope, known_facts[], assumptions[], unknowns[], exclusions[], budget{{max_cost_usd,max_wall_seconds,max_run_seconds,max_retries,max_sessions}}, stop_conditions[].",
        "evidence_ledger": f'''schema_version={VERSION}; exact top-level keys: schema_version, queries, claims, evidence, unresolved. Query exact keys: id, backend, endpoint, parameters{{}}, executed_at, result_count, status (completed|partial|failed), snapshot{{path,sha256}}; snapshot is mandatory even for failed queries and must point under research/evidence/snapshots/. Claim exact keys: id,text. Evidence exact keys: id,claim_ids[],source{{url,persistent_id,title}},retrieval_query_id,accessed_at,stance (supports|contradicts|neutral|mixed),verification_status (verified|metadata-only|unresolved|blocked). Minimal shape: {{"schema_version":"{VERSION}","queries":[{{"id":"Q1","backend":"...","endpoint":"...","parameters":{{}},"executed_at":"...","result_count":0,"status":"failed","snapshot":{{"path":"research/evidence/snapshots/Q1.json","sha256":"<64 hex>"}}}}],"claims":[{{"id":"C1","text":"..."}}],"evidence":[{{"id":"E1","claim_ids":["C1"],"source":{{"url":"https://...","persistent_id":"...","title":"..."}},"retrieval_query_id":"Q1","accessed_at":"...","stance":"neutral","verification_status":"unresolved"}}],"unresolved":["..."]}}.''',
        "hypothesis_ledger": f'''schema_version={VERSION}; exact top-level keys: schema_version, research_question, hypotheses, measurements. hypotheses must contain at least one object. Each hypothesis has exactly: id, null, alternative, predictions, rivals, falsifier, evidence_ids. id is unique. null, alternative and falsifier are non-empty strings. predictions and rivals are non-empty string arrays. evidence_ids is a non-empty unique array containing only Evidence Record IDs from evidence-ledger.evidence[].id (normally E-prefixed); Claim IDs from claims[].id (normally C-prefixed) are forbidden. A candidate without evidence support must remain unresolved and must not be promoted into hypotheses. measurements is a non-empty string array. Minimal shape: {{"schema_version":"{VERSION}","research_question":"...","hypotheses":[{{"id":"H1","null":"...","alternative":"...","predictions":["..."],"rivals":["..."],"falsifier":"...","evidence_ids":["E1"]}}],"measurements":["..."]}}.''',
        "experiment_plan": f'''schema_version={VERSION}; exact top-level keys: schema_version, hypothesis_ids, conditions, tasks, metrics, repetitions, design, budget, stop_conditions, analysis. hypothesis_ids: unique known IDs. conditions: >=2 unique strings or objects with id. tasks: >=4 across >=3 categories; each task must contain id, category, deterministic_grader=true, hidden_tests=true (additional descriptive task fields are allowed). metrics: each item has name and primary; exactly one primary metric. repetitions: positive integer. design requires unit_of_analysis, replication_unit, assignment, randomization, blocking, seeds[], data_split, leakage_controls[]. budget requires max_cost_usd, max_wall_seconds, max_run_seconds, max_retries, max_sessions and may tighten but never exceed the research contract. The full factorial count len(tasks)*len(conditions)*repetitions must not exceed max_sessions. analysis requires primary_metric, multiplicity, missing_data, sensitivity; primary_metric must match the declared primary metric. Minimal shape: {{"schema_version":"{VERSION}","hypothesis_ids":["H1"],"conditions":["A","B"],"tasks":[{{"id":"T1","category":"implementation","deterministic_grader":true,"hidden_tests":true}},{{"id":"T2","category":"debugging","deterministic_grader":true,"hidden_tests":true}},{{"id":"T3","category":"analysis","deterministic_grader":true,"hidden_tests":true}},{{"id":"T4","category":"review","deterministic_grader":true,"hidden_tests":true}}],"metrics":[{{"name":"pass_rate","primary":true}}],"repetitions":1,"design":{{"unit_of_analysis":"task-condition run","replication_unit":"independent session","assignment":"paired","randomization":"seeded order","blocking":"task","seeds":[42],"data_split":"hidden tests","leakage_controls":["hidden graders"]}},"budget":{{"max_cost_usd":1.0,"max_wall_seconds":600,"max_run_seconds":180,"max_retries":0,"max_sessions":8}},"stop_conditions":["budget reached"],"analysis":{{"primary_metric":"pass_rate","multiplicity":"report all","missing_data":"count failure","sensitivity":"paired effects"}}}}.''',
        "implementation": f"schema_version={VERSION}; execution_id; executor{{type=local,protocol_version=1,max_parallel=1,failure_policy}}; entrypoint exactly [\"python3\",\"scripts/execute-approved.py\"]; trials[] with trial_id,condition,task_id,seed,parameters; estimated_sessions equals trial count; budget; frozen_inputs[]; environment{{python{{version,executable}},dependency_lock{{path,sha256,reason}},code_snapshot[],data_artifacts[],pass_env[]}}. Entrypoint must accept --trial-spec and --result. Do not start measured sessions.",
        "smoke_result": f"schema_version={VERSION}; passed=true, checks[], measured_sessions_started=0, projected_cost_usd>=0, projected_wall_seconds>=0.",
        "formal_result": f"schema_version={VERSION}; execution_id, outcome, sessions_started, sessions_completed, cost_usd, wall_seconds, budget_exceeded, trial_ids[], trial_statuses, result_hashes, result_files, checkpoint_path.",
        "analysis_result": f"schema_version={VERSION}; hypothesis_ids[], primary_metric, effect_estimate, uncertainty, task_coverage, limitations[], deviations[].",
        "decision": f"schema_version={VERSION}; outcome, requested_next, claim_ids[], hypothesis_ids[], trial_ids[], reason.",
    }
    return contracts.get(validator_name or "", "No additional structured artifact.")


def command_doctor(_: argparse.Namespace) -> int:
    problems: list[str] = []
    if sys.version_info < (3, 10):
        problems.append("Python 3.10+ required")
    opencode = shutil.which("opencode")
    if not opencode:
        problems.append("opencode not found in PATH")
    try:
        workflow = load_json(WORKFLOW_PATH)
        if workflow["version"] != VERSION:
            problems.append("VERSION and workflow version differ")
        if workflow["initial"] not in workflow["phases"]:
            problems.append("workflow initial phase is missing")
    except Exception as exc:
        problems.append(f"invalid workflow: {exc}")
    problems.extend(skill_lock_problems())
    try:
        registry = load_json(SCHEMA_REGISTRY_PATH)
        if registry.get("schema_version") != VERSION:
            problems.append("schema registry version differs from VERSION")
        for schema_name in registry.get("artifacts", {}).values():
            schema = load_json(ROOT / "schemas" / schema_name)
            if schema.get("properties", {}).get("schema_version", {}).get("const") != VERSION:
                problems.append(f"schema version differs: {schema_name}")
        for schema_name in registry.get("supporting", {}).values():
            schema = load_json(ROOT / "schemas" / schema_name)
            if schema.get("properties", {}).get("schema_version", {}).get("const") != VERSION:
                problems.append(f"supporting schema version differs: {schema_name}")
        provenance_schema = load_json(ROOT / "schemas" / "provenance-record.schema.json")
        if provenance_schema.get("properties", {}).get("schema_version", {}).get("const") != VERSION:
            problems.append("provenance schema version differs from VERSION")
    except Exception as exc:
        problems.append(f"invalid schema registry: {exc}")
    for runtime_name in ("executor.py", "local_executor.py", "guarded_runner.py"):
        if not (ROOT / "runtime" / runtime_name).is_file():
            problems.append(f"missing runtime: {runtime_name}")
    for agent in ("research-worker", "research-builder", "research-executor", "research-critic", "research-repairer", "research-reviser"):
        if not (ROOT / ".opencode" / "agents" / f"{agent}.md").is_file():
            problems.append(f"missing agent: {agent}")
    print(f"Research Mode: {VERSION}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"OpenCode: {opencode or 'missing'}")
    if problems:
        for item in problems:
            print(f"ERROR: {item}", file=sys.stderr)
        return 1
    print("Doctor: PASS")
    return 0


def command_new(args: argparse.Namespace) -> int:
    project = project_path(args.project_id)
    if project.exists() and any(project.iterdir()):
        raise SystemExit(f"project already exists and is not empty: {project}")
    for relative in (".research/logs", ".research/results", ".research/runtime", ".research/provenance", ".research/approvals", ".research/revisions/hypotheses", "research/evidence/snapshots", "src", "scripts", "configs", "tasks", "runs", "results", "report"):
        (project / relative).mkdir(parents=True, exist_ok=True)
    for runtime_name in ("executor.py", "local_executor.py", "guarded_runner.py"):
        shutil.copy2(ROOT / "runtime" / runtime_name, project / ".research" / "runtime" / runtime_name)
    # Keep the vetted retrieval/parsing tools inside the project boundary. This
    # lets the evidence phase use them without reaching into an external Skill
    # directory, while the source Skill tree remains hash-locked.
    shutil.copytree(
        ROOT / ".agents" / "skills" / "paper-lookup" / "scripts",
        project / ".research" / "runtime" / "paper-lookup",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (project / "RESEARCH_GOAL.md").write_text(
        f"# Research goal\n\n{args.goal.strip()}\n\n## Initial constraints\n\n{(args.constraints or 'Not specified. Resolve during framing.').strip()}\n",
        encoding="utf-8",
    )
    state = {
        "project_id": args.project_id,
        "workflow": "computational-research",
        "workflow_version": VERSION,
        "phase": "framing",
        "status": "ready",
        "attempts": 0,
        "interruptions": 0,
        "model": args.model,
        "blocker": None,
        "active_run": None,
        "approved_budget": None,
        "frozen_manifest_sha256": None,
        "created_at": now(),
        "updated_at": now(),
    }
    save_state(project, state)
    event(project, "project_created", goal=args.goal, model=args.model, workflow_version=VERSION)
    print(project)
    return 0


def build_prompt(project: Path, phase_name: str, phase: dict[str, Any]) -> str:
    skills = ", ".join(phase.get("skills", [])) or "none"
    required = "\n".join(f"- {item}" for item in phase.get("required_files", []))
    transition = phase.get("next") or "one of: " + ", ".join(phase["allowed_next"])
    return f"""You are executing exactly one phase of Research Mode {VERSION}.

Current phase: {phase_name}
Purpose: {phase['purpose']}
Load these skills when available: {skills}
{('Project-local vetted tools: ' + ', '.join(phase.get('runtime_tools', []))) if phase.get('runtime_tools') else ''}

Read RESEARCH_GOAL.md, STATUS.md and only relevant prior artifacts.
Required artifacts:
{required}

Structured contract:
{phase_json_contract(phase.get('validator'))}

Allowed requested_next: {transition}

Execute only this phase. Do not ask questions, skip ahead, install software, access external directories, use remote compute, delete data or change approved scope. Keep human Markdown concise; put repeated records in JSON. If a missing decision affects validity, return blocked instead of guessing.

Finally write .research/phase-result.json with exactly:
phase, status, summary, artifacts, checks, requested_next, blocker.
Summary <= 2000 characters; checks <= 20. Use completed only when every artifact exists.
"""


def structured_schema(validator_name: str | None) -> str:
    registry = load_json(SCHEMA_REGISTRY_PATH).get("artifacts", {})
    relative = VALIDATORS.get(validator_name or "", (None, None))[0]
    schema_name = registry.get(relative) if relative else None
    if not schema_name:
        return "{}"
    return (ROOT / "schemas" / schema_name).read_text(encoding="utf-8").strip()


def build_repair_prompt(
    project: Path,
    phase_name: str,
    phase: dict[str, Any],
    validation_error: str,
) -> str:
    allowed = [item for item in phase.get("required_files", []) if item.endswith(".json")] + [".research/phase-result.json"]
    policy = phase.get("repair_policy")
    if policy == "evidence-ledger":
        policy_instructions = '''Preserve IDs, query parameters, result counts, claim meaning, source identity, evidence links, access dates and unresolved items. Do not add or remove claims, queries, evidence records or sources. Do not modify existing snapshot files. Map an existing `statement` claim field to `text` without rewriting it. Allowed enum mappings are: query success->completed, error->failed; stance supports_contextual->supports; verification bibliographic-verified->metadata-only, text-unverified->unresolved. For a missing failed-query snapshot, write exactly {"repair_record":true,"original_response_available":false,"query_id":"<query-id>","reason":"<non-empty reason from the existing record or validation error>","results":[]} to research/evidence/snapshots/<query-id>-repair-failure.json; then reference its SHA-256. This record documents missing raw data and is not evidence.'''
        extra_allowed = "- research/evidence/snapshots/<query-id>-repair-failure.json only when that query's prior snapshot was null or missing"
    elif policy == "experiment-plan":
        policy_instructions = '''Preserve hypothesis IDs, condition identity and order, task identity/order/category/specification, grader requirements, metric definitions, design, budget, stop conditions and analysis meaning. Do not add or remove conditions, tasks, metrics, hypotheses, design commitments, budget limits or stop conditions. You may map task_id to id without rewriting the value. You may replace a repetitions object with a positive integer only when it contains an unambiguous positive per_cell value, or when total_trials exactly equals len(tasks)*len(conditions)*the inferred repetitions; otherwise return blocked. Do not increase max_sessions or weaken any budget, leakage control, grader, hidden-test or stopping constraint. Additional descriptive fields inside tasks, metrics, conditions, design, budget and analysis may remain.'''
        extra_allowed = ""
    elif policy == "hypothesis-references":
        policy_instructions = '''Preserve the research question, measurements, hypothesis IDs/order, nulls, alternatives, predictions, rivals and falsifiers exactly. Do not add, remove or rewrite hypotheses. In evidence_ids only, replace each Claim ID with every Evidence Record ID whose claim_ids contains that Claim ID, using evidence-ledger evidence order; retain existing Evidence Record IDs and remove duplicates while preserving first occurrence. Do not guess a mapping. If any invalid reference is not a known Claim ID with at least one linked Evidence Record, return blocked.'''
        extra_allowed = ""
    else:
        policy_instructions = "No safe repair policy is configured. Return blocked."
        extra_allowed = ""
    return f"""You are executing a bounded structural repair in Research Mode {VERSION}.

Current phase: {phase_name}
Repair mode: true
Deterministic validation errors from the prior attempt:
{validation_error}

Exact JSON Schema:
{structured_schema(phase.get('validator'))}

Allowed files to modify or create:
{chr(10).join('- ' + item for item in allowed)}
{extra_allowed}

Repair only structural ABI violations covered by the phase policy. Markdown and declared inputs are read-only. Do not search, fetch, run experiments, load Skills or access external directories.

Phase repair policy:
{policy_instructions}

If any repair requires scientific judgment or new evidence, return blocked instead of guessing.

Finally write .research/phase-result.json with exactly:
phase, status, summary, artifacts, checks, requested_next, blocker.
Use status=completed only when all required artifacts are present. requested_next must be {phase['next']}.
"""


def build_revision_prompt(
    phase_name: str,
    phase: dict[str, Any],
    validation_error: str,
) -> str:
    return f"""You are executing a bounded grounded revision in Research Mode {VERSION}.

Current phase: {phase_name}
Revision mode: true
Deterministic validation errors from the rejected attempt:
{validation_error}

Exact JSON Schema:
{structured_schema(phase.get('validator'))}

Allowed files to modify or create:
- research/hypothesis-ledger.md
- research/hypothesis-ledger.json
- .research/revision-diff.json
- .research/phase-result.json

Use only research/research-contract.json, research/evidence-ledger.json and the rejected hypothesis ledger already present. These inputs are frozen. Do not search, fetch, load Skills, run experiments, add evidence, access external directories or modify any other file.

Preserve research_question and measurements exactly. Preserve IDs and order for retained hypotheses and do not add hypotheses. You may remove an unsupported hypothesis, or rewrite its null, alternative, predictions, rivals, falsifier and evidence_ids using only existing Evidence Record IDs from evidence-ledger.evidence[].id. Empty or unknown evidence_ids must never be filled by invention. At least one valid falsifiable hypothesis must remain; otherwise return blocked.

Write .research/revision-diff.json with exactly:
schema_version, phase, mode, validation_error, changes, evidence_ids_used.
mode must be grounded-revision. validation_error must exactly equal the controller error above. changes must include exactly every revised or removed hypothesis, each with hypothesis_id, action (revised|removed), reason, before_evidence_ids and after_evidence_ids. evidence_ids_used must list the unique Evidence Record IDs used by the final ledger in first-use order.

Finally write .research/phase-result.json with exactly:
phase, status, summary, artifacts, checks, requested_next, blocker.
Use status=completed only when both hypothesis artifacts and revision diff are present. artifacts must list only research/hypothesis-ledger.md and research/hypothesis-ledger.json. requested_next must be {phase['next']}.
"""


def capture_invalid_attempt(
    project: Path,
    phase_name: str,
    phase: dict[str, Any],
    run_id: str,
    reason: str,
    failure_class: str = "semantic_validation",
    source_project: Path | None = None,
) -> None:
    source_root = source_project or project
    destination = project / ".research" / "invalid" / phase_name / run_id
    copied: list[str] = []
    for relative in list(phase.get("required_files", [])) + [".research/phase-result.json"]:
        source = source_root / relative
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(relative)
    atomic_json(
        destination / "failure.json",
        {"schema_version": VERSION, "phase": phase_name, "run_id": run_id, "failure_class": failure_class, "reason": reason, "artifacts": copied},
    )


def file_snapshot(paths: list[Path], root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for base in paths:
        candidates = [base] if base.is_file() else sorted(base.rglob("*")) if base.is_dir() else []
        for path in candidates:
            if path.is_file():
                result[str(path.relative_to(root))] = file_sha256(path)
    return result


def create_repair_workspace(project: Path, phase: dict[str, Any]) -> tuple[Path, Path]:
    temporary_root = Path(tempfile.mkdtemp(prefix="research-mode-repair-"))
    workspace = temporary_root / "project"
    workspace.mkdir()
    relatives = {"RESEARCH_GOAL.md", "STATUS.md", ".research/phase-result.json"}
    relatives.update(phase.get("inputs", []))
    relatives.update(phase.get("required_files", []))
    for relative in sorted(relatives):
        source = project / relative
        if source.is_file():
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    snapshots = project / "research" / "evidence" / "snapshots"
    if snapshots.is_dir():
        shutil.copytree(snapshots, workspace / "research" / "evidence" / "snapshots", dirs_exist_ok=True)
    return temporary_root, workspace


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + f".{os.getpid()}.tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def repair_artifact_path(phase: dict[str, Any]) -> str:
    candidates = [item for item in phase.get("required_files", []) if item.endswith(".json")]
    if len(candidates) != 1:
        raise ValueError("repairable phase must declare exactly one JSON artifact")
    return candidates[0]


def promote_repair(project: Path, workspace: Path, phase: dict[str, Any], before: dict[str, Any]) -> None:
    for relative in (repair_artifact_path(phase), ".research/phase-result.json"):
        atomic_copy(safe_project_file(workspace, relative), project / relative)
    if phase.get("repair_policy") != "evidence-ledger":
        return
    for query in before.get("queries", []):
        if not isinstance(query, dict) or isinstance(query.get("snapshot"), dict):
            continue
        query_id = query.get("id")
        if not isinstance(query_id, str) or not SAFE_IDENTIFIER.fullmatch(query_id):
            raise ValueError("cannot promote repair snapshot for an invalid query id")
        relative = f"research/evidence/snapshots/{query_id}-repair-failure.json"
        atomic_copy(safe_project_file(workspace, relative, prefix="research/evidence/snapshots/"), project / relative)


def promote_evidence_repair(project: Path, workspace: Path, phase: dict[str, Any], before: dict[str, Any]) -> None:
    """Compatibility wrapper retained for downstream imports."""
    promote_repair(project, workspace, phase, before)


def evidence_repair_fingerprint(data: dict[str, Any]) -> dict[str, Any]:
    """Scientific content that a structural repair is not allowed to change."""
    claims = [
        {"id": item.get("id"), "text": item.get("text", item.get("statement"))}
        for item in data.get("claims", []) if isinstance(item, dict)
    ]
    queries = [
        {key: item.get(key) for key in ("id", "backend", "endpoint", "parameters", "executed_at", "result_count")}
        for item in data.get("queries", []) if isinstance(item, dict)
    ]
    evidence = [
        {key: item.get(key) for key in ("id", "claim_ids", "source", "retrieval_query_id", "accessed_at")}
        for item in data.get("evidence", []) if isinstance(item, dict)
    ]
    return {"claims": claims, "queries": queries, "evidence": evidence, "unresolved": data.get("unresolved")}


def validate_evidence_repair(
    project: Path,
    before: dict[str, Any],
    before_snapshots: dict[str, str],
) -> None:
    after = load_json(project / "research" / "evidence-ledger.json")
    if evidence_repair_fingerprint(after) != evidence_repair_fingerprint(before):
        raise ValueError("repair changed scientific content, record identity or ordering")
    prior_queries = {item.get("id"): item for item in before.get("queries", []) if isinstance(item, dict)}
    after_queries = {item.get("id"): item for item in after.get("queries", []) if isinstance(item, dict)}
    for query_id, prior in prior_queries.items():
        repaired = after_queries.get(query_id, {})
        old_snapshot = prior.get("snapshot")
        new_snapshot = repaired.get("snapshot")
        if isinstance(old_snapshot, dict):
            if new_snapshot != old_snapshot:
                raise ValueError(f"repair changed existing snapshot binding for query {query_id}")
            continue
        if not isinstance(query_id, str) or not SAFE_IDENTIFIER.fullmatch(query_id):
            raise ValueError("cannot safely name a repair snapshot for an invalid query id")
        expected = f"research/evidence/snapshots/{query_id}-repair-failure.json"
        if not isinstance(new_snapshot, dict) or new_snapshot.get("path") != expected:
            raise ValueError(f"missing query {query_id} must use the controlled repair snapshot path")
        record = load_json(safe_project_file(project, expected, prefix="research/evidence/snapshots/"))
        if set(record) != {"repair_record", "original_response_available", "query_id", "reason", "results"}:
            raise ValueError(f"repair snapshot for query {query_id} has invalid keys")
        if record.get("repair_record") is not True or record.get("original_response_available") is not False:
            raise ValueError(f"repair snapshot for query {query_id} misstates response availability")
        if record.get("query_id") != query_id or not isinstance(record.get("reason"), str) or not record["reason"].strip() or record.get("results") != []:
            raise ValueError(f"repair snapshot for query {query_id} is invalid")
    current = file_snapshot([project / "research" / "evidence" / "snapshots"], project)
    for relative, digest in before_snapshots.items():
        if current.get(relative) != digest:
            raise ValueError(f"repair modified or removed existing snapshot: {relative}")
    expected_new = {
        f"research/evidence/snapshots/{query_id}-repair-failure.json"
        for query_id, query in prior_queries.items()
        if not isinstance(query.get("snapshot"), dict)
    }
    if set(current) - set(before_snapshots) != expected_new:
        raise ValueError("repair created an unapproved snapshot file")


def canonical_repetitions(value: Any, data: dict[str, Any]) -> Any:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if not isinstance(value, dict):
        return value
    allowed = {"per_cell", "total_trials"}
    if not set(value) <= allowed or not value:
        return value
    per_cell = value.get("per_cell")
    if isinstance(per_cell, int) and not isinstance(per_cell, bool) and per_cell > 0:
        expected = len(data.get("tasks", [])) * len(data.get("conditions", [])) * per_cell
        total = value.get("total_trials", expected)
        return per_cell if total == expected else value
    total = value.get("total_trials")
    cells = len(data.get("tasks", [])) * len(data.get("conditions", []))
    if isinstance(total, int) and not isinstance(total, bool) and total > 0 and cells > 0 and total % cells == 0:
        return total // cells
    return value


def experiment_plan_repair_fingerprint(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize only explicitly permitted aliases before comparing scientific meaning."""
    tasks: list[Any] = []
    for item in data.get("tasks", []) if isinstance(data.get("tasks"), list) else []:
        if not isinstance(item, dict):
            tasks.append(item)
            continue
        normalized = {key: value for key, value in item.items() if key not in {"id", "task_id"}}
        normalized["id"] = item.get("id", item.get("task_id"))
        tasks.append(normalized)
    return {
        "hypothesis_ids": data.get("hypothesis_ids"),
        "conditions": data.get("conditions"),
        "tasks": tasks,
        "metrics": data.get("metrics"),
        "repetitions": canonical_repetitions(data.get("repetitions"), data),
        "design": data.get("design"),
        "budget": data.get("budget"),
        "stop_conditions": data.get("stop_conditions"),
        "analysis": data.get("analysis"),
    }


def validate_experiment_plan_repair(project: Path, before: dict[str, Any]) -> None:
    after = load_json(project / "research" / "experiment-plan.json")
    if experiment_plan_repair_fingerprint(after) != experiment_plan_repair_fingerprint(before):
        raise ValueError("repair changed experiment design, scientific content or ordering")


def claim_to_evidence(project: Path) -> dict[str, list[str]]:
    ledger = load_json(project / "research" / "evidence-ledger.json")
    mapping: dict[str, list[str]] = {}
    for item in ledger.get("evidence", []):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        for claim_id in item.get("claim_ids", []):
            if isinstance(claim_id, str):
                mapping.setdefault(claim_id, []).append(item["id"])
    return mapping


def canonical_hypothesis_references(project: Path, data: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(data))
    mapping = claim_to_evidence(project)
    for item in normalized.get("hypotheses", []):
        if not isinstance(item, dict) or not isinstance(item.get("evidence_ids"), list):
            continue
        linked: list[Any] = []
        for reference in item["evidence_ids"]:
            candidates = mapping.get(reference, [reference])
            for candidate in candidates:
                if candidate not in linked:
                    linked.append(candidate)
        item["evidence_ids"] = linked
    return normalized


def validate_hypothesis_reference_repair(project: Path, before: dict[str, Any]) -> None:
    after = load_json(project / "research" / "hypothesis-ledger.json")
    if after != canonical_hypothesis_references(project, before):
        raise ValueError("repair changed hypothesis content or did not apply the deterministic claim-to-evidence mapping")


def hypothesis_recovery_kind(project: Path, phase: dict[str, Any]) -> str | None:
    try:
        rejected = load_json(project / repair_artifact_path(phase))
        issues = hypothesis_validation_issues(rejected, project)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if issues and all(code == "claim_reference" for code, _ in issues):
        mapping = claim_to_evidence(project)
        claim_ids = {
            reference
            for item in rejected.get("hypotheses", []) if isinstance(item, dict)
            for reference in item.get("evidence_ids", []) if isinstance(reference, str) and reference in mapping
        }
        return "repair" if claim_ids and all(mapping.get(claim_id) for claim_id in claim_ids) else None
    hypotheses = rejected.get("hypotheses")
    if not phase.get("revisionable") or not isinstance(hypotheses, list) or not hypotheses:
        return None
    identifiers = [item.get("id") for item in hypotheses if isinstance(item, dict)]
    if len(identifiers) != len(hypotheses) or any(not isinstance(item, str) or not item.strip() for item in identifiers):
        return None
    if len(set(identifiers)) != len(identifiers):
        return None
    return "revision"


def ordered_final_evidence_ids(data: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for item in data.get("hypotheses", []):
        if not isinstance(item, dict):
            continue
        for evidence_id in item.get("evidence_ids", []):
            if isinstance(evidence_id, str) and evidence_id not in result:
                result.append(evidence_id)
    return result


def validate_hypothesis_revision(workspace: Path, before: dict[str, Any], validation_error: str) -> None:
    after = load_json(workspace / "research" / "hypothesis-ledger.json")
    record = load_json(workspace / ".research" / "revision-diff.json")
    validate_hypothesis_ledger(after, workspace)
    if after.get("research_question") != before.get("research_question") or after.get("measurements") != before.get("measurements"):
        raise ValueError("revision changed the research question or measurements")
    before_items = before.get("hypotheses", [])
    after_items = after.get("hypotheses", [])
    before_by_id = {item.get("id"): item for item in before_items if isinstance(item, dict)}
    after_by_id = {item.get("id"): item for item in after_items if isinstance(item, dict)}
    before_order = [item.get("id") for item in before_items if isinstance(item, dict)]
    after_order = [item.get("id") for item in after_items if isinstance(item, dict)]
    if not set(after_order) <= set(before_order):
        raise ValueError("revision added a hypothesis")
    if after_order != [item for item in before_order if item in after_by_id]:
        raise ValueError("revision changed retained hypothesis ordering")
    changed = {item for item in before_order if item not in after_by_id or before_by_id[item] != after_by_id[item]}
    expected_keys = {"schema_version", "phase", "mode", "validation_error", "changes", "evidence_ids_used"}
    if set(record) != expected_keys or record.get("schema_version") != VERSION:
        raise ValueError("revision diff has invalid top-level shape or version")
    if record.get("phase") != "hypotheses" or record.get("mode") != "grounded-revision":
        raise ValueError("revision diff identifies the wrong phase or mode")
    if record.get("validation_error") != validation_error:
        raise ValueError("revision diff does not preserve the controller validation error")
    changes = record.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError("revision diff must describe at least one change")
    described: set[str] = set()
    for index, change in enumerate(changes):
        keys = {"hypothesis_id", "action", "reason", "before_evidence_ids", "after_evidence_ids"}
        if not isinstance(change, dict) or set(change) != keys:
            raise ValueError(f"revision diff changes[{index}] has invalid shape")
        hypothesis_id = change.get("hypothesis_id")
        if hypothesis_id not in changed or hypothesis_id in described:
            raise ValueError(f"revision diff changes[{index}] does not identify exactly one changed hypothesis")
        described.add(hypothesis_id)
        action = "removed" if hypothesis_id not in after_by_id else "revised"
        if change.get("action") != action:
            raise ValueError(f"revision diff changes[{index}].action is inconsistent")
        if not isinstance(change.get("reason"), str) or not change["reason"].strip():
            raise ValueError(f"revision diff changes[{index}].reason is empty")
        if change.get("before_evidence_ids") != before_by_id[hypothesis_id].get("evidence_ids"):
            raise ValueError(f"revision diff changes[{index}] misstates prior evidence IDs")
        expected_after = [] if action == "removed" else after_by_id[hypothesis_id].get("evidence_ids")
        if change.get("after_evidence_ids") != expected_after:
            raise ValueError(f"revision diff changes[{index}] misstates final evidence IDs")
    if described != changed:
        raise ValueError("revision diff omits a changed hypothesis")
    if record.get("evidence_ids_used") != ordered_final_evidence_ids(after):
        raise ValueError("revision diff evidence_ids_used does not match the final ledger")


def repair_allowed_changes(phase: dict[str, Any], before: dict[str, Any]) -> set[str]:
    allowed = {repair_artifact_path(phase), ".research/phase-result.json"}
    if phase.get("repair_policy") == "evidence-ledger":
        allowed.update(
            f"research/evidence/snapshots/{query.get('id')}-repair-failure.json"
            for query in before.get("queries", [])
            if isinstance(query, dict) and not isinstance(query.get("snapshot"), dict)
        )
    return allowed


def validate_repair_boundary(
    workspace: Path,
    phase: dict[str, Any],
    before: dict[str, Any],
    before_snapshots: dict[str, str],
) -> None:
    policy = phase.get("repair_policy")
    if policy == "evidence-ledger":
        validate_evidence_repair(workspace, before, before_snapshots)
    elif policy == "experiment-plan":
        validate_experiment_plan_repair(workspace, before)
    elif policy == "hypothesis-references":
        validate_hypothesis_reference_repair(workspace, before)
    else:
        raise ValueError(f"unsupported repair policy: {policy!r}")


def revision_allowed_changes(phase: dict[str, Any]) -> set[str]:
    return set(phase.get("required_files", [])) | {".research/phase-result.json", ".research/revision-diff.json"}


def promote_revision(project: Path, workspace: Path, phase: dict[str, Any], run_id: str) -> str:
    for relative in list(phase.get("required_files", [])) + [".research/phase-result.json"]:
        atomic_copy(safe_project_file(workspace, relative), project / relative)
    destination = project / ".research" / "revisions" / "hypotheses" / f"{run_id}.json"
    atomic_copy(safe_project_file(workspace, ".research/revision-diff.json"), destination)
    return str(destination.relative_to(project))


def validate_result(project: Path, workflow: dict[str, Any], phase_name: str, phase: dict[str, Any], result: dict[str, Any]) -> tuple[bool, str, str | None]:
    keys = {"phase", "status", "summary", "artifacts", "checks", "requested_next", "blocker"}
    if set(result) != keys:
        return False, f"phase result keys must equal {sorted(keys)}", None
    if result["phase"] != phase_name:
        return False, "phase result names a different phase", None
    if result["status"] not in {"completed", "blocked"}:
        return False, "status must be completed or blocked", None
    if not isinstance(result["summary"], str) or not result["summary"].strip():
        return False, "summary is empty", None
    if len(result["summary"]) > workflow["max_summary_chars"]:
        return False, "summary exceeds configured limit", None
    if not isinstance(result["checks"], list) or len(result["checks"]) > workflow["max_checks"]:
        return False, "checks is invalid or too long", None
    if not isinstance(result["artifacts"], list):
        return False, "artifacts must be an array", None
    if ".research/phase-result.json" in result["artifacts"]:
        return False, ".research/phase-result.json is a mutable controller mailbox, not a provenance artifact", None
    if result["status"] == "blocked":
        return (bool(result["blocker"]), "blocked result requires blocker" if not result["blocker"] else "blocked", None)
    missing = [relative for relative in phase.get("required_files", []) if not (project / relative).is_file()]
    if missing:
        return False, "missing required artifacts: " + ", ".join(missing), None
    if not set(phase.get("required_files", [])) <= set(result["artifacts"]):
        return False, "artifacts must list every required file", None
    try:
        for relative in result["artifacts"]:
            safe_project_file(project, relative)
    except ValueError as exc:
        return False, str(exc), None
    try:
        semantic_validate(project, phase.get("validator"))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return False, f"semantic validation failed: {exc}", None
    requested = result["requested_next"]
    if "next" in phase and requested != phase["next"]:
        return False, f"requested_next must be {phase['next']}", None
    if "allowed_next" in phase and requested not in phase["allowed_next"]:
        return False, "requested_next is not allowed", None
    return True, "ok", requested


def fail_attempt(
    project: Path,
    state: dict[str, Any],
    workflow: dict[str, Any],
    phase_name: str,
    run_id: str,
    message: str,
    failure_class: str = "semantic_validation",
) -> None:
    state["attempts"] = state.get("attempts", 0) + 1
    state["active_run"] = None
    event(project, "phase_failed", phase=phase_name, run_id=run_id, attempt=state["attempts"], failure_class=failure_class, reason=message)
    if state["attempts"] >= workflow["max_attempts_per_phase"]:
        state.update(status="blocked", blocker=message, phase="blocked")
    else:
        state.update(status="ready", blocker=message)
    save_state(project, state)


def recover_interrupted(project: Path, state: dict[str, Any]) -> None:
    if state.get("status") != "running":
        return
    prior = state.get("active_run") or {}
    state["interruptions"] = state.get("interruptions", 0) + 1
    state["status"] = "ready"
    state["active_run"] = None
    state["blocker"] = "previous runner ended without a terminal event; safely resumed"
    event(project, "phase_recovered", phase=state["phase"], prior_run_id=prior.get("run_id"), prior_pid=prior.get("pid"))
    save_state(project, state)


def phase_timeout(project: Path, phase: dict[str, Any]) -> int:
    if not phase.get("timeout_from_budget"):
        return int(phase.get("timeout_seconds", 1800))
    plan = load_json(project / "research" / "experiment-plan.json")
    return int(plan["budget"]["max_wall_seconds"]) + 300


def execute_phase(project: Path, state: dict[str, Any], workflow: dict[str, Any]) -> bool:
    phase_name = state["phase"]
    phase = workflow["phases"][phase_name]
    if phase.get("type") == "human_gate":
        if state["status"] != "waiting_approval":
            state["status"] = "waiting_approval"
            save_state(project, state)
            event(project, "approval_requested", phase=phase_name, prompt=phase["prompt"])
        print(f"WAITING APPROVAL: {phase['prompt']}")
        return False
    if phase.get("verify_freeze"):
        valid, reason = verify_freeze(project)
        if not valid:
            state.update(phase="blocked", status="blocked", blocker=reason, active_run=None)
            event(project, "freeze_verification_failed", phase=phase_name, reason=reason)
            save_state(project, state)
            return False
        authorized, authorization_reason = verify_execution_authorization(project)
        if not authorized:
            state.update(phase="blocked", status="blocked", blocker=authorization_reason, active_run=None)
            event(project, "execution_authorization_failed", phase=phase_name, reason=authorization_reason)
            save_state(project, state)
            return False
    try:
        input_snapshot = declared_input_snapshot(project, phase)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        reason = f"dependency validation failed: {exc}"
        state.update(phase="blocked", status="blocked", blocker=reason, active_run=None)
        event(project, "dependency_invalidated", phase=phase_name, reason=reason)
        save_state(project, state)
        return False
    semantic_retry = bool(
        state.get("attempts", 0) > 0
        and isinstance(state.get("blocker"), str)
        and state["blocker"].startswith("semantic validation failed:")
    )
    recovery_kind: str | None = None
    if semantic_retry and phase_name == "hypotheses":
        recovery_kind = hypothesis_recovery_kind(project, phase)
    elif semantic_retry and phase.get("repairable"):
        recovery_kind = "repair"
    repairing = recovery_kind == "repair"
    revisioning = recovery_kind == "revision"
    repair_before: dict[str, Any] | None = None
    repair_snapshots: dict[str, str] = {}
    repair_workspace_before: dict[str, str] = {}
    repair_temp_root: Path | None = None
    execution_project = project
    if repairing or revisioning:
        try:
            repair_before = load_json(project / repair_artifact_path(phase))
            if phase.get("repair_policy") == "evidence-ledger":
                repair_snapshots = file_snapshot([project / "research" / "evidence" / "snapshots"], project)
            repair_temp_root, execution_project = create_repair_workspace(project, phase)
            repair_workspace_before = file_snapshot([execution_project], execution_project)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            reason = f"{'revision' if revisioning else 'repair'} precondition failed: {exc}"
            state.update(phase="blocked", status="blocked", blocker=reason, active_run=None)
            event(project, "phase_revision_refused" if revisioning else "phase_repair_refused", phase=phase_name, reason=reason)
            save_state(project, state)
            return False

    def cleanup_repair_workspace() -> None:
        if repair_temp_root is not None:
            shutil.rmtree(repair_temp_root, ignore_errors=True)

    result_path = execution_project / ".research" / "phase-result.json"
    if result_path.exists():
        result_path.unlink()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = uuid.uuid4().hex
    log_path = project / ".research" / "logs" / f"{stamp}-{phase_name}-{run_id[:8]}.jsonl"
    validation_error = str(state.get("blocker") or "")
    if revisioning:
        prompt = build_revision_prompt(phase_name, phase, validation_error)
        selected_agent = phase.get("revision_agent", "research-reviser")
    elif repairing:
        prompt = build_repair_prompt(project, phase_name, phase, validation_error)
        selected_agent = phase.get("repair_agent", "research-repairer")
    else:
        prompt = build_prompt(project, phase_name, phase)
        selected_agent = phase.get("agent", "research-worker")
    command = ["opencode", "run", "--auto", "--format", "json", "--agent", selected_agent, "--dir", str(execution_project)]
    if state.get("model"):
        command.extend(["--model", state["model"]])
    command.append(prompt)
    state.update(status="running", blocker=None, active_run={"run_id": run_id, "pid": os.getpid(), "phase": phase_name, "started_at": now(), "log": str(log_path.relative_to(project))})
    save_state(project, state)
    event_name = "phase_revision_started" if revisioning else "phase_repair_started" if repairing else "phase_started"
    event(project, event_name, phase=phase_name, run_id=run_id, attempt=state.get("attempts", 0) + 1, agent=selected_agent, model=state.get("model"))
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, text=True, check=False, timeout=phase_timeout(project, phase))
    except subprocess.TimeoutExpired:
        capture_invalid_attempt(project, phase_name, phase, run_id, "phase timed out", "timeout", source_project=execution_project)
        cleanup_repair_workspace()
        fail_attempt(project, state, workflow, phase_name, run_id, f"phase timed out; see {log_path.relative_to(project)}", "timeout")
        return state["phase"] not in TERMINALS
    if completed.returncode != 0:
        capture_invalid_attempt(project, phase_name, phase, run_id, f"OpenCode exited {completed.returncode}", "process_exit", source_project=execution_project)
        cleanup_repair_workspace()
        fail_attempt(project, state, workflow, phase_name, run_id, f"OpenCode exited {completed.returncode}; see {log_path.relative_to(project)}", "process_exit")
        return state["phase"] not in TERMINALS
    if not result_path.is_file():
        capture_invalid_attempt(project, phase_name, phase, run_id, "OpenCode did not create .research/phase-result.json", "missing_phase_result", source_project=execution_project)
        cleanup_repair_workspace()
        fail_attempt(project, state, workflow, phase_name, run_id, "OpenCode did not create .research/phase-result.json", "missing_phase_result")
        return state["phase"] not in TERMINALS
    try:
        result = load_json(result_path)
    except Exception as exc:
        capture_invalid_attempt(project, phase_name, phase, run_id, f"invalid phase result JSON: {exc}", "invalid_phase_result", source_project=execution_project)
        cleanup_repair_workspace()
        fail_attempt(project, state, workflow, phase_name, run_id, f"invalid phase result JSON: {exc}", "invalid_phase_result")
        return state["phase"] not in TERMINALS
    valid, reason, requested = validate_result(execution_project, workflow, phase_name, phase, result)
    if not valid:
        failure_class = "semantic_validation" if reason.startswith("semantic validation failed:") else "invalid_phase_result"
        capture_invalid_attempt(project, phase_name, phase, run_id, reason, failure_class, source_project=execution_project)
        cleanup_repair_workspace()
        fail_attempt(project, state, workflow, phase_name, run_id, reason, failure_class)
        return state["phase"] not in TERMINALS
    if (repairing or revisioning) and result["status"] == "blocked":
        blocker = str(result["blocker"])
        recovery_label = "revision" if revisioning else "repair"
        capture_invalid_attempt(project, phase_name, phase, run_id, f"{recovery_label} agent blocked: {blocker}", "semantic_validation", source_project=execution_project)
        cleanup_repair_workspace()
        state.update(phase="blocked", status="blocked", blocker=blocker, attempts=0, active_run=None)
        event(project, "phase_revision_blocked" if revisioning else "phase_repair_blocked", phase=phase_name, run_id=run_id, blocker=blocker)
        save_state(project, state)
        return False
    try:
        current_inputs = [{"path": item["path"], "sha256": file_sha256(safe_project_file(execution_project, item["path"]))} for item in input_snapshot]
        if current_inputs != input_snapshot:
            raise ValueError("phase modified a declared input")
        revision_record_path: str | None = None
        if repairing and repair_before is not None:
            validate_repair_boundary(execution_project, phase, repair_before, repair_snapshots)
            current_workspace = file_snapshot([execution_project], execution_project)
            changed_paths = {
                relative
                for relative in set(repair_workspace_before) | set(current_workspace)
                if repair_workspace_before.get(relative) != current_workspace.get(relative)
            }
            allowed_changes = repair_allowed_changes(phase, repair_before)
            if not changed_paths <= allowed_changes:
                raise ValueError("repair modified a file outside its allowed paths")
            promote_repair(project, execution_project, phase, repair_before)
        elif revisioning and repair_before is not None:
            validate_hypothesis_revision(execution_project, repair_before, validation_error)
            current_workspace = file_snapshot([execution_project], execution_project)
            changed_paths = {
                relative
                for relative in set(repair_workspace_before) | set(current_workspace)
                if repair_workspace_before.get(relative) != current_workspace.get(relative)
            }
            if not changed_paths <= revision_allowed_changes(phase):
                raise ValueError("revision modified a file outside its allowed paths")
            revision_record_path = promote_revision(project, execution_project, phase, run_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        reason = f"revision boundary validation failed: {exc}" if revisioning else f"repair boundary validation failed: {exc}" if repairing else f"input immutability failed: {exc}"
        capture_invalid_attempt(project, phase_name, phase, run_id, reason, "boundary_violation", source_project=execution_project)
        cleanup_repair_workspace()
        fail_attempt(project, state, workflow, phase_name, run_id, reason, "boundary_violation")
        return state["phase"] not in TERMINALS
    cleanup_repair_workspace()
    if phase.get("verify_freeze"):
        frozen, freeze_reason = verify_freeze(project)
        if not frozen:
            fail_attempt(project, state, workflow, phase_name, run_id, freeze_reason)
            return state["phase"] not in TERMINALS
        authorized, authorization_reason = verify_execution_authorization(project)
        if not authorized:
            fail_attempt(project, state, workflow, phase_name, run_id, authorization_reason)
            return state["phase"] not in TERMINALS
    try:
        provenance_artifacts = (set(phase.get("required_files", [])) | set(result["artifacts"])) - {".research/phase-result.json"}
        if revision_record_path:
            provenance_artifacts.add(revision_record_path)
        write_provenance(
            project,
            phase_name,
            phase,
            state,
            run_id,
            prompt,
            sorted(provenance_artifacts),
            input_snapshot,
            generator_agent=selected_agent,
            generator_skills=[] if repairing or revisioning else None,
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        fail_attempt(project, state, workflow, phase_name, run_id, f"provenance generation failed: {exc}", "provenance_failure")
        return state["phase"] not in TERMINALS
    if repairing:
        event(project, "phase_repair_completed", phase=phase_name, run_id=run_id, policy=phase.get("repair_policy"))
    if revisioning:
        event(project, "phase_revision_completed", phase=phase_name, run_id=run_id, policy=phase.get("revision_policy"), revision_record=revision_record_path, revision_sha256=file_sha256(project / revision_record_path))
    atomic_json(project / ".research" / "results" / f"{stamp}-{phase_name}-{run_id[:8]}.json", result)
    state["active_run"] = None
    if result["status"] == "blocked":
        state.update(phase="blocked", status="blocked", blocker=result["blocker"], attempts=0)
        event(project, "project_blocked", from_phase=phase_name, run_id=run_id, blocker=result["blocker"])
        save_state(project, state)
        return False
    next_status = "completed" if requested == "done" else requested if requested in {"blocked", "terminated"} else "ready"
    next_blocker = result["summary"] if requested in {"blocked", "terminated"} else None
    state.update(phase=requested, status=next_status, blocker=next_blocker, attempts=0)
    event(project, "phase_completed", phase=phase_name, run_id=run_id, next=requested, artifacts=result["artifacts"])
    save_state(project, state)
    return requested not in TERMINALS


def command_run(args: argparse.Namespace) -> int:
    project = project_path(args.project_id)
    with ProjectLock(project):
        state = load_json(state_path(project))
        if state.get("workflow_version") != VERSION:
            raise SystemExit(f"project uses workflow {state.get('workflow_version')}; Research Mode is {VERSION}. Preserve the old project and create a new one.")
        workflow = load_json(WORKFLOW_PATH)
        recover_interrupted(project, state)
        if state["phase"] in TERMINALS:
            print(f"terminal state: {state['phase']}")
            return 0 if state["phase"] == "done" else 2
        for _ in range(args.max_steps):
            if not execute_phase(project, state, workflow):
                break
            state = load_json(state_path(project))
        final = load_json(state_path(project))
        print(f"{final['project_id']}: {final['status']} at {final['phase']}")
        return 0 if final["phase"] != "blocked" else 2


def approve_gate(project: Path, state: dict[str, Any], phase: dict[str, Any], note: str) -> None:
    old = state["phase"]
    try:
        declared_input_snapshot(project, phase)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"approval refused because provenance is invalid: {exc}") from exc
    if old == "plan-approval":
        plan = load_json(project / "research" / "experiment-plan.json")
        validate_experiment_plan(plan, project)
        state["approved_budget"] = plan["budget"]
    if phase.get("freeze"):
        manifest_data = load_json(project / "configs" / "execution-manifest.json")
        validate_implementation(manifest_data, project)
        smoke_data = load_json(project / "runs" / "smoke-test" / "result.json")
        validate_smoke_result(smoke_data, project)
        approved = state.get("approved_budget")
        if manifest_data["budget"] != approved:
            raise SystemExit("execution manifest budget differs from the plan-approved budget")
        if smoke_data["projected_cost_usd"] > approved["max_cost_usd"]:
            raise SystemExit("projected cost exceeds approved budget")
        if smoke_data["projected_wall_seconds"] > approved["max_wall_seconds"]:
            raise SystemExit("projected wall time exceeds approved budget")
        environment_lock = build_environment_lock(project, manifest_data)
        environment_lock_path = project / ".research" / "environment-lock.json"
        atomic_json(environment_lock_path, environment_lock)
        freeze = build_freeze_manifest(project)
        freeze_path = project / ".research" / "frozen-manifest.json"
        atomic_json(freeze_path, freeze)
        state["frozen_manifest_sha256"] = file_sha256(freeze_path)
        authorization = {
            "schema_version": VERSION,
            "execution_id": manifest_data["execution_id"],
            "approved_at": now(),
            "approved_note": note,
            "budget": approved,
            "execution_manifest_sha256": file_sha256(project / "configs" / "execution-manifest.json"),
            "frozen_manifest_sha256": state["frozen_manifest_sha256"],
            "environment_lock_sha256": file_sha256(environment_lock_path),
        }
        atomic_json(project / ".research" / "execution-authorization.json", authorization)
        event(project, "inputs_frozen", phase=old, file_count=len(freeze["files"]), manifest_sha256=state["frozen_manifest_sha256"])
    approval = {
        "schema_version": VERSION,
        "phase": old,
        "approved_at": now(),
        "note": note,
        "approved_next": phase["approved_next"],
    }
    atomic_json(project / ".research" / "approvals" / f"{old}.json", approval)
    state.update(phase=phase["approved_next"], status="ready", blocker=None, attempts=0, active_run=None)
    event(project, "approval_granted", phase=old, note=note)
    save_state(project, state)


def command_approve(args: argparse.Namespace) -> int:
    project = project_path(args.project_id)
    with ProjectLock(project):
        state = load_json(state_path(project))
        workflow = load_json(WORKFLOW_PATH)
        phase = workflow["phases"].get(state["phase"], {})
        if phase.get("type") != "human_gate":
            raise SystemExit(f"current phase is not a human gate: {state['phase']}")
        approve_gate(project, state, phase, args.note)
        print(f"approved: {args.project_id} -> {state['phase']}")
    return 0


def command_reject(args: argparse.Namespace) -> int:
    project = project_path(args.project_id)
    with ProjectLock(project):
        state = load_json(state_path(project))
        workflow = load_json(WORKFLOW_PATH)
        phase = workflow["phases"].get(state["phase"], {})
        if phase.get("type") != "human_gate":
            raise SystemExit(f"current phase is not a human gate: {state['phase']}")
        old = state["phase"]
        state.update(phase=phase["rejected_next"], status="ready", blocker=args.note, attempts=0, active_run=None, frozen_manifest_sha256=None)
        event(project, "approval_rejected", phase=old, note=args.note)
        save_state(project, state)
        print(f"rejected: {old} -> {state['phase']}")
    return 0


def command_status(args: argparse.Namespace) -> int:
    print(json.dumps(load_json(state_path(project_path(args.project_id))), ensure_ascii=False, indent=2))
    return 0


def audit_project(args: argparse.Namespace, project: Path) -> int:
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[str] = []
    try:
        state = load_json(state_path(project))
        if state.get("workflow_version") != VERSION:
            errors.append(f"project workflow is {state.get('workflow_version')}, controller is {VERSION}")
        else:
            checks.append("workflow-version")
    except Exception as exc:
        state = {"project_id": args.project_id}
        errors.append(f"state: {exc}")
    lock_problems = skill_lock_problems()
    if lock_problems:
        errors.extend(f"skill-lock: {item}" for item in lock_problems)
    else:
        checks.append("skill-lock")
    try:
        lock_entries = skill_lock_entries()
        pending = sorted(name for name, item in lock_entries.items() if item["status"] == "reviewed-pending-package")
        if pending:
            warnings.append("reviewed skills not packaged: " + ", ".join(pending))
    except Exception:
        pass
    try:
        registry = load_json(SCHEMA_REGISTRY_PATH)
        for relative in registry["artifacts"]:
            path = project / relative
            if not path.is_file():
                continue
            validator_name = next((name for name, (candidate, _) in VALIDATORS.items() if candidate == relative), None)
            if validator_name:
                try:
                    semantic_validate(project, validator_name)
                    checks.append(f"semantic:{relative}")
                except Exception as exc:
                    errors.append(f"semantic:{relative}: {exc}")
            sidecar = provenance_path(project, relative)
            if not sidecar.is_file():
                errors.append(f"provenance missing: {relative}")
    except Exception as exc:
        errors.append(f"schema-registry: {exc}")
    provenance_root = project / ".research" / "provenance"
    for sidecar in sorted(provenance_root.rglob("*.provenance.json")) if provenance_root.is_dir() else []:
        try:
            validate_provenance_record(project, load_json(sidecar))
            checks.append(f"provenance:{sidecar.relative_to(provenance_root)}")
        except Exception as exc:
            errors.append(f"provenance:{sidecar.relative_to(project)}: {exc}")
    freeze_path = project / ".research" / "frozen-manifest.json"
    if freeze_path.is_file():
        valid, reason = verify_freeze(project)
        if valid:
            checks.append("frozen-inputs")
        else:
            errors.append(f"frozen-inputs: {reason}")
        authorized, authorization_reason = verify_execution_authorization(project)
        if authorized:
            checks.append("execution-authorization")
        else:
            errors.append(f"execution-authorization: {authorization_reason}")
    report = {
        "schema_version": VERSION,
        "project_id": state.get("project_id", args.project_id),
        "audited_at": now(),
        "status": "PASS" if not errors else "FAIL",
        "checks": sorted(set(checks)),
        "errors": errors,
        "warnings": warnings,
    }
    atomic_json(project / "research" / "audit-report.json", report)
    event(project, "project_audited", status=report["status"], errors=len(errors), warnings=len(warnings))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Audit: {report['status']} ({len(report['checks'])} checks, {len(errors)} errors, {len(warnings)} warnings)")
        for item in errors:
            print(f"ERROR: {item}", file=sys.stderr)
        for item in warnings:
            print(f"WARNING: {item}")
    return 0 if not errors else 2


def command_audit(args: argparse.Namespace) -> int:
    project = project_path(args.project_id)
    with ProjectLock(project):
        return audit_project(args, project)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=f"OpenCode Research Mode {VERSION}")
    commands = result.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor")
    doctor.set_defaults(func=command_doctor)
    new = commands.add_parser("new")
    new.add_argument("project_id")
    new.add_argument("--goal", required=True)
    new.add_argument("--constraints")
    new.add_argument("--model", help="OpenCode provider/model-id; omit to use current default")
    new.set_defaults(func=command_new)
    run = commands.add_parser("run")
    run.add_argument("project_id")
    run.add_argument("--max-steps", type=int, default=20)
    run.set_defaults(func=command_run)
    approve = commands.add_parser("approve")
    approve.add_argument("project_id")
    approve.add_argument("--note", required=True)
    approve.set_defaults(func=command_approve)
    reject = commands.add_parser("reject")
    reject.add_argument("project_id")
    reject.add_argument("--note", required=True)
    reject.set_defaults(func=command_reject)
    status = commands.add_parser("status")
    status.add_argument("project_id")
    status.set_defaults(func=command_status)
    audit = commands.add_parser("audit")
    audit.add_argument("project_id")
    audit.add_argument("--json", action="store_true")
    audit.set_defaults(func=command_audit)
    return result


if __name__ == "__main__":
    arguments = parser().parse_args()
    raise SystemExit(arguments.func(arguments))
