"""Sandbox contract: run a command inside an isolated job directory.

All compilation and execution of COBOL and (agent-generated) Java goes
through a :class:`Sandbox`. A job directory is the only thing a command can
rely on: callers copy every input into it first, and every output is read
back from it afterwards. Backends decide how strongly that directory is
isolated (see ``docker.py`` and ``wsl.py``).
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class SandboxError(RuntimeError):
    """The sandbox itself failed (not the command it ran)."""


@dataclass(frozen=True)
class Limits:
    """Resource limits applied to every command."""

    timeout_s: float = 120.0
    max_file_mb: int = 256  # largest file a command may write
    max_procs: int = 256
    java_heap_mb: int = 512


@dataclass
class ExecResult:
    """Outcome of one sandboxed command."""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    backend: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class Sandbox(Protocol):
    """Something that can run a command in a job directory."""

    name: str
    isolates_network: bool

    def run(
        self,
        argv: list[str],
        workdir: Path,
        *,
        timeout_s: float | None = None,
        network: bool = False,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        """Run ``argv`` with ``workdir`` as the current directory."""
        ...

    def available(self) -> bool:
        """True if the backend can run commands on this host."""
        ...


#: Environment every sandboxed command sees (on top of the backend's own).
DETERMINISTIC_ENV = {
    "TZ": "UTC",
    "LC_ALL": "C",
    "LANG": "C",
    "SOURCE_DATE_EPOCH": "0",
}


def new_job_dir(base: Path, label: str) -> Path:
    """Create a fresh, empty job directory under ``base``."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    path = base / f"{safe}-{uuid.uuid4().hex[:10]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def remove_job_dir(path: Path) -> None:
    """Delete a job directory (best effort)."""
    shutil.rmtree(path, ignore_errors=True)
