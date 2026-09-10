#!/usr/bin/env python3
"""Stable executor interface for approved Research Mode trials."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class ExecutionBlocked(RuntimeError):
    """The execution cannot continue without changing an approved input."""


class ExecutionTerminated(RuntimeError):
    """The execution stopped at an approved resource or safety boundary."""


class Executor(ABC):
    """Run an approved manifest and return its aggregate formal result."""

    protocol_version = "1"

    def __init__(
        self,
        project: Path,
        manifest: dict[str, Any],
        authorization: dict[str, Any],
    ) -> None:
        self.project = project.resolve()
        self.manifest = manifest
        self.authorization = authorization

    @abstractmethod
    def execute(self) -> dict[str, Any]:
        """Execute or resume the manifest without repeating completed trials."""

