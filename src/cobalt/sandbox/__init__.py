"""Sandboxed execution of COBOL and Java (shared by equivalence and harness)."""

from __future__ import annotations

import os

from cobalt.config import Settings
from cobalt.sandbox.backends import DockerSandbox, LocalSandbox, WslSandbox, to_wsl_path
from cobalt.sandbox.base import (
    ExecResult,
    Limits,
    Sandbox,
    SandboxError,
    new_job_dir,
    remove_job_dir,
)

__all__ = [
    "DockerSandbox",
    "ExecResult",
    "Limits",
    "LocalSandbox",
    "Sandbox",
    "SandboxError",
    "WslSandbox",
    "get_sandbox",
    "new_job_dir",
    "remove_job_dir",
    "to_wsl_path",
]

_cache: dict[str, Sandbox] = {}


def get_sandbox(settings: Settings, backend: str | None = None) -> Sandbox:
    """Pick a sandbox backend.

    ``auto`` prefers: local inside the dev container, then Docker, then WSL.
    Raises :class:`SandboxError` if nothing usable is found.
    """
    choice = (backend or settings.sandbox).lower()
    if choice in _cache:
        return _cache[choice]
    limits = Limits(timeout_s=settings.exec_timeout_s)
    candidates: list[Sandbox]
    if choice == "local":
        candidates = [LocalSandbox(limits)]
    elif choice == "docker":
        candidates = [DockerSandbox(settings.docker_image, limits)]
    elif choice == "wsl":
        candidates = [WslSandbox(settings.wsl_distro, settings.wsl_env_script, limits)]
    elif choice == "auto":
        candidates = []
        if os.environ.get("COBALT_IN_DOCKER") == "1":
            candidates.append(LocalSandbox(limits))
        candidates.append(DockerSandbox(settings.docker_image, limits))
        if os.name == "nt":
            candidates.append(WslSandbox(settings.wsl_distro, settings.wsl_env_script, limits))
    else:
        raise SandboxError(f"unknown sandbox backend {choice!r}")
    for candidate in candidates:
        if candidate.available():
            _cache[choice] = candidate
            return candidate
    tried = ", ".join(c.name for c in candidates)
    raise SandboxError(
        f"no sandbox available (tried: {tried}). Build the image with `make build`, "
        "or on Windows run `bash scripts/wsl_toolchain.sh` inside WSL."
    )
