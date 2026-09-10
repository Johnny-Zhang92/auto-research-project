#!/usr/bin/env python3
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StaticPolicyTests(unittest.TestCase):
    def test_workflow_has_two_gates(self) -> None:
        workflow = json.loads((ROOT / "workflow/research-workflow.json").read_text())
        self.assertEqual(workflow["version"], (ROOT / "VERSION").read_text().strip())
        self.assertEqual(workflow["phases"]["experiment-plan"]["next"], "plan-approval")
        self.assertEqual(workflow["phases"]["smoke-test"]["next"], "execution-approval")
        self.assertEqual(workflow["phases"]["execution-approval"]["approved_next"], "formal-experiment")
        self.assertTrue(workflow["phases"]["execution-approval"]["freeze"])

    def test_no_generic_script_shell_permission(self) -> None:
        agents = "\n".join(path.read_text() for path in (ROOT / ".opencode/agents").glob("*.md"))
        self.assertNotIn('"bash scripts/*": allow', agents)
        self.assertNotIn('"bash *": allow', agents)

    def test_executor_only_exposes_guarded_entrypoint(self) -> None:
        executor = (ROOT / ".opencode/agents/research-executor.md").read_text()
        self.assertIn('"python3 .research/runtime/guarded_runner.py": allow', executor)
        self.assertNotIn('"python3 scripts/execute-approved.py": allow', executor)

    def test_local_executor_runtime_is_frozen(self) -> None:
        controller = (ROOT / "researchctl.py").read_text()
        self.assertIn('".research/runtime"', controller)
        self.assertIn('("executor.py", "local_executor.py", "guarded_runner.py")', controller)

    def test_formal_phase_verifies_frozen_inputs(self) -> None:
        workflow = json.loads((ROOT / "workflow/research-workflow.json").read_text())
        formal = workflow["phases"]["formal-experiment"]
        self.assertTrue(formal["verify_freeze"])
        self.assertTrue(formal["timeout_from_budget"])

    def test_every_phase_declares_inputs(self) -> None:
        workflow = json.loads((ROOT / "workflow/research-workflow.json").read_text())
        for name, phase in workflow["phases"].items():
            if name in {"done", "blocked", "terminated"}:
                continue
            self.assertIn("inputs", phase, name)
            self.assertIsInstance(phase["inputs"], list, name)

    def test_schema_registry_is_versioned_and_complete(self) -> None:
        version = (ROOT / "VERSION").read_text().strip()
        registry = json.loads((ROOT / "schemas/schema-registry.json").read_text())
        self.assertEqual(registry["schema_version"], version)
        workflow = json.loads((ROOT / "workflow/research-workflow.json").read_text())
        structured = {
            path
            for phase in workflow["phases"].values()
            for path in phase.get("required_files", [])
            if path.endswith(".json")
        }
        self.assertEqual(set(registry["artifacts"]), structured)
        for schema_name in registry["artifacts"].values():
            schema = json.loads((ROOT / "schemas" / schema_name).read_text())
            self.assertEqual(schema["properties"]["schema_version"]["const"], version)
        self.assertEqual(set(registry["supporting"]), {"trial-result", "executor-state", "environment-lock"})
        for schema_name in registry["supporting"].values():
            schema = json.loads((ROOT / "schemas" / schema_name).read_text())
            self.assertEqual(schema["properties"]["schema_version"]["const"], version)

    def test_skill_lock_has_admitted_v03_skills(self) -> None:
        lock = json.loads((ROOT / "skills.lock.json").read_text())
        entries = {item["name"]: item for item in lock["skills"]}
        for name in ("paper-lookup", "experimental-design", "peer-review"):
            self.assertEqual(entries[name]["status"], "enabled")
            self.assertRegex(entries[name]["tree_sha256"], r"^[0-9a-f]{64}$")

    def test_admitted_skill_identity_and_paths(self) -> None:
        lock = json.loads((ROOT / "skills.lock.json").read_text())
        entries = {item["name"]: item for item in lock["skills"]}
        for name in ("paper-lookup", "experimental-design", "peer-review"):
            directory = ROOT / ".agents" / "skills" / name
            text = (directory / "SKILL.md").read_text()
            self.assertRegex(text, rf"(?m)^name: {re.escape(name)}$")
            self.assertRegex(text, rf'(?m)^  version: "?{re.escape(entries[name]["version"])}"?$')
            self.assertFalse(any(path.is_symlink() for path in directory.rglob("*")), name)

    def test_new_skills_are_routed_without_cross_mode_leakage(self) -> None:
        phases = json.loads((ROOT / "workflow/research-workflow.json").read_text())["phases"]
        self.assertIn("paper-lookup", phases["evidence"]["skills"])
        self.assertIn("experimental-design", phases["experiment-plan"]["skills"])
        for phase_name, phase in phases.items():
            if phase_name != "evidence":
                self.assertNotIn("paper-lookup", phase.get("skills", []), phase_name)
            if phase_name != "experiment-plan":
                self.assertNotIn("experimental-design", phase.get("skills", []), phase_name)
            # Formal peer review has its own mandatory authorization/intake gate.
            self.assertNotIn("peer-review", phase.get("skills", []), phase_name)

    def test_evidence_uses_project_local_vetted_tools(self) -> None:
        evidence = json.loads((ROOT / "workflow/research-workflow.json").read_text())["phases"]["evidence"]
        self.assertIn(".research/runtime/paper-lookup/paginate.py", evidence["runtime_tools"])
        controller = (ROOT / "researchctl.py").read_text()
        self.assertIn('"paper-lookup" / "scripts"', controller)

    def test_phase_repairs_are_networkless_and_bounded(self) -> None:
        workflow = json.loads((ROOT / "workflow/research-workflow.json").read_text())
        for name, policy in (("evidence", "evidence-ledger"), ("hypotheses", "hypothesis-references"), ("experiment-plan", "experiment-plan")):
            phase = workflow["phases"][name]
            self.assertTrue(phase["repairable"])
            self.assertEqual(phase["repair_agent"], "research-repairer")
            self.assertEqual(phase["repair_policy"], policy)
        repairer = (ROOT / ".opencode/agents/research-repairer.md").read_text()
        self.assertIn("websearch: deny", repairer)
        self.assertIn("webfetch: deny", repairer)
        self.assertIn("skill: deny", repairer)
        self.assertIn("Do not add or remove claims", (ROOT / "researchctl.py").read_text())
        self.assertIn("Do not add or remove conditions", (ROOT / "researchctl.py").read_text())

    def test_hypothesis_revision_is_grounded_and_networkless(self) -> None:
        phase = json.loads((ROOT / "workflow/research-workflow.json").read_text())["phases"]["hypotheses"]
        self.assertTrue(phase["revisionable"])
        self.assertEqual(phase["revision_agent"], "research-reviser")
        self.assertEqual(phase["revision_policy"], "grounded-hypotheses")
        reviser = (ROOT / ".opencode/agents/research-reviser.md").read_text()
        for rule in ("websearch: deny", "webfetch: deny", "skill: deny", "external_directory: deny"):
            self.assertIn(rule, reviser)
        self.assertIn("do not add hypotheses", reviser.lower())

    def test_phase_result_is_not_a_provenance_artifact(self) -> None:
        controller = (ROOT / "researchctl.py").read_text()
        self.assertIn("mutable controller mailbox, not a provenance artifact", controller)
        self.assertIn('- {".research/phase-result.json"}', controller)

    def test_peer_review_remains_local_and_intake_gated(self) -> None:
        skill = (ROOT / ".agents/skills/peer-review/SKILL.md").read_text()
        self.assertIn("## Mandatory safety boundary", skill)
        self.assertIn("READY_FOR_LOCAL_REVIEW", skill)
        critic = (ROOT / ".opencode/agents/research-critic.md").read_text()
        self.assertIn("internal scientific audit", critic.lower())
        self.assertIn("Do not load `peer-review`", critic)


if __name__ == "__main__":
    unittest.main()
