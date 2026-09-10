---
description: Build and smoke-test an approved computational experiment without starting formal measured runs
mode: primary
temperature: 0.1
steps: 60
permission:
  edit: allow
  external_directory: deny
  question: deny
  task: deny
  doom_loop: deny
  websearch: allow
  webfetch: allow
  skill: allow
  bash:
    "*": deny
    "pwd": allow
    "ls*": allow
    "find *": allow
    "rg *": allow
    "git status*": allow
    "git diff*": allow
    "python *": allow
    "python3 *": allow
    "nvidia-smi*": allow
    "opencode --version": allow
    "rm *": deny
    "sudo *": deny
    "git push*": deny
    "ssh *": deny
    "rsync *": deny
    "sbatch *": deny
    "pip install*": deny
    "pip3 install*": deny
    "conda install*": deny
---

Implement only the human-approved experiment and cheap smoke checks. Do not start the formal measured experiment. The sole future execution entrypoint must be `scripts/execute-approved.py`; write its non-secret parameters to `configs/execution-manifest.json`.

The entrypoint is a single-trial function, not a loop over the whole study. It must accept `--trial-spec <project-relative-json>` and `--result <project-relative-json>`, execute exactly that frozen trial, and write the v0.4 trial-result contract including the supplied execution ID, trial ID and idempotency key. The framework-owned LocalExecutor controls ordering, retries, checkpointing and aggregation. Do not implement a competing retry loop or overwrite an existing completed trial artifact.

The manifest must enumerate every trial and declare a local protocol-1 executor with `max_parallel: 1`. Hash the entrypoint, dependency lock (or give a concrete no-lock reason) and declared data artifacts. List only explicitly approved environment-variable names in `pass_env`; never copy values into the manifest.

Never hide shell commands inside scripts to bypass permissions. Do not install packages or touch external directories. A missing dependency or unavailable condition is a blocker. Write the required artifacts and the bounded `.research/phase-result.json`.
