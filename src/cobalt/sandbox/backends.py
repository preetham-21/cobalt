"""Sandbox backends.

``docker``  Each command runs in a throwaway container of the dev image with
            only the job directory mounted, no network, capped memory/CPU/PIDs,
            and a read-only root filesystem. Strongest isolation.
``wsl``     For Windows hosts without Docker: the command runs inside a WSL
            distribution under ``unshare -rn`` (a rootless network namespace:
            no network), ``timeout`` and ``prlimit``, using the rootless
            toolchain from ``scripts/wsl_toolchain.sh``. The filesystem is not
            isolated, so callers keep inputs inside the job directory.
``local``   Plain subprocess. Only for use *inside* the dev container (where
            the container is the sandbox) or for trusted, non-agent code.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path, PureWindowsPath

from cobalt.sandbox.base import DETERMINISTIC_ENV, ExecResult, Limits, SandboxError

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _decode(data: bytes | None) -> str:
    return (data or b"").decode("utf-8", errors="replace")


def _run(
    argv: list[str], timeout_s: float, *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> tuple[int, str, str, float, bool]:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout=timeout_s + 15,
            check=False,
            creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        return 124, _decode(exc.stdout), _decode(exc.stderr), time.monotonic() - start, True
    except OSError as exc:
        raise SandboxError(f"cannot start {argv[0]}: {exc}") from exc
    elapsed = time.monotonic() - start
    # `timeout` exits 124 (or 137 after -k) when it had to stop the command
    timed_out = proc.returncode in (124, 137) and elapsed >= timeout_s - 0.5
    return proc.returncode, _decode(proc.stdout), _decode(proc.stderr), elapsed, timed_out


class LocalSandbox:
    """Run directly with subprocess (inside the dev container only)."""

    name = "local"
    isolates_network = False

    def __init__(self, limits: Limits | None = None) -> None:
        self.limits = limits or Limits()

    def available(self) -> bool:
        return shutil.which("cobc") is not None and shutil.which("java") is not None

    def run(
        self,
        argv: list[str],
        workdir: Path,
        *,
        timeout_s: float | None = None,
        network: bool = False,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        timeout = timeout_s or self.limits.timeout_s
        full_env = {**os.environ, **DETERMINISTIC_ENV, **(env or {})}
        wrapped = argv
        if shutil.which("timeout"):
            wrapped = ["timeout", "-k", "5", str(int(timeout)), *argv]
        rc, out, err, dur, to = _run(wrapped, timeout, cwd=workdir, env=full_env)
        return ExecResult(argv, rc, out, err, dur, to, self.name)


def to_wsl_path(path: Path) -> str:
    """``C:\\Users\\x`` -> ``/mnt/c/Users/x`` (POSIX paths pass through)."""
    p = PureWindowsPath(path)
    if not p.drive:
        return Path(path).as_posix()
    drive = p.drive.rstrip(":").lower()
    rest = "/".join(p.parts[1:])
    return f"/mnt/{drive}/{rest}"


class WslSandbox:
    """Run inside a WSL distribution with no network and resource limits."""

    name = "wsl"
    isolates_network = True

    def __init__(self, distro: str, env_script: str, limits: Limits | None = None) -> None:
        self.distro = distro
        self.env_script = env_script
        self.limits = limits or Limits()
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is None:
            if shutil.which("wsl.exe") is None and shutil.which("wsl") is None:
                self._available = False
            else:
                probe = (
                    f"test -f {self.env_script} && . {self.env_script} && "
                    "command -v cobc >/dev/null && command -v java >/dev/null"
                )
                rc, *_ = _run(["wsl.exe", "-d", self.distro, "--exec", "bash", "-c", probe], 60)
                self._available = rc == 0
        return self._available

    def run(
        self,
        argv: list[str],
        workdir: Path,
        *,
        timeout_s: float | None = None,
        network: bool = False,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        timeout = int(timeout_s or self.limits.timeout_s)
        exports = " ".join(
            f"export {k}={shlex.quote(v)};" for k, v in {**DETERMINISTIC_ENV, **(env or {})}.items()
        )
        limits = (
            f"prlimit --fsize={self.limits.max_file_mb * 1024 * 1024} "
            f"--nproc={self.limits.max_procs} --"
        )
        isolate = "" if network else "unshare -rn"
        script = (
            f". {self.env_script} >/dev/null 2>&1; {exports} "
            f"cd {shlex.quote(to_wsl_path(workdir))} && "
            f"exec {isolate} timeout -k 5 {timeout} {limits} {shlex.join(argv)}"
        )
        wsl_argv = ["wsl.exe", "-d", self.distro, "--exec", "bash", "-c", script]
        rc, out, err, dur, to = _run(wsl_argv, timeout)
        return ExecResult(argv, rc, out, err, dur, to, self.name)


class DockerSandbox:
    """Throwaway container per command, job directory as the only mount."""

    name = "docker"
    isolates_network = True

    def __init__(self, image: str, limits: Limits | None = None) -> None:
        self.image = image
        self.limits = limits or Limits()
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is None:
            if shutil.which("docker") is None:
                self._available = False
            else:
                rc, *_ = _run(["docker", "image", "inspect", self.image], 30)
                self._available = rc == 0
        return self._available

    def run(
        self,
        argv: list[str],
        workdir: Path,
        *,
        timeout_s: float | None = None,
        network: bool = False,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        timeout = int(timeout_s or self.limits.timeout_s)
        env_args: list[str] = []
        for k, v in {**DETERMINISTIC_ENV, **(env or {})}.items():
            env_args += ["-e", f"{k}={v}"]
        docker_argv = [
            "docker",
            "run",
            "--rm",
            "--network",
            "bridge" if network else "none",
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--pids-limit",
            str(self.limits.max_procs),
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=512m",
            "--security-opt",
            "no-new-privileges",
            "-v",
            f"{Path(workdir).resolve()}:/work",
            "-w",
            "/work",
            *env_args,
            "--entrypoint",
            "timeout",
            self.image,
            "-k",
            "5",
            str(timeout),
            *argv,
        ]
        rc, out, err, dur, to = _run(docker_argv, timeout)
        return ExecResult(argv, rc, out, err, dur, to, self.name)
