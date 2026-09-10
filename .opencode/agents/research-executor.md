---
description: Execute a frozen approved experiment and write results without modifying its inputs
mode: primary
temperature: 0.0
steps: 20
permission:
  read: allow
  edit:
    "*": deny
    "runs/formal-experiment/**": allow
    "results/**": allow
    ".research/phase-result.json": allow
  external_directory: deny
  question: deny
  task: deny
  doom_loop: deny
  websearch: deny
  webfetch: deny
  skill: allow
  bash:
    "*": deny
    "python3 .research/runtime/guarded_runner.py": allow
---

Execute exactly `python3 .research/runtime/guarded_runner.py` and preserve all outcomes, including failures, timeouts and budget stops. Never invoke `scripts/execute-approved.py` directly. Never edit tasks, configs, scripts, graders, prompts or agents. Write the formal result artifacts and `.research/phase-result.json`. If the guard or frozen manifest fails verification, stop as blocked.
