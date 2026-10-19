"""Runtime configuration, loaded from the environment and ``.env``.

All settings use the ``COBALT_`` prefix except the API key, which keeps the
Anthropic SDK's conventional name ``ANTHROPIC_API_KEY``. The key is held as a
``SecretStr`` so it never appears in reprs, dumps or logs.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_repo_root(start: Path | None = None) -> Path:
    """Return the repository root.

    ``COBALT_ROOT`` wins if set; otherwise walk up from ``start`` (default: the
    current directory) to the first directory holding ``pyproject.toml`` and
    ``corpus/``. Falls back to ``start`` itself.
    """
    env_root = os.environ.get("COBALT_ROOT")
    if env_root:
        return Path(env_root).resolve()
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "corpus").is_dir():
            return candidate
    return here


class Settings(BaseSettings):
    """cobalt settings. Construct via :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_prefix="COBALT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    root: Path = Field(default_factory=find_repo_root)
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY")
    )
    model: str = "claude-opus-5-5"
    log_level: str = "INFO"

    # execution sandbox (see cobalt.sandbox)
    sandbox: str = "auto"  # auto | docker | wsl | local
    docker_image: str = "cobalt-dev:0.1.0"
    wsl_distro: str = "Ubuntu"
    wsl_env_script: str = "$HOME/.local/cobalt-toolchain/env.sh"
    exec_timeout_s: float = 120.0

    # optional relocation of generated artefacts (tests, CI)
    runs_root: Path | None = None
    workspace_root: Path | None = None

    @property
    def corpus_dir(self) -> Path:
        """Directory holding all corpora."""
        return self.root / "corpus"

    @property
    def runs_dir(self) -> Path:
        """Directory for logs, traces and reports."""
        return self.runs_root or self.root / "runs"

    @property
    def logs_dir(self) -> Path:
        """Directory for structured JSON logs."""
        return self.runs_dir / "logs"

    @property
    def workspace_dir(self) -> Path:
        """Directory for generated Java projects."""
        return self.workspace_root or self.root / "workspace"

    def secret_values(self) -> list[str]:
        """Raw secret strings, for log redaction only. Never print these."""
        if self.anthropic_api_key is None:
            return []
        value = self.anthropic_api_key.get_secret_value()
        return [value] if value else []


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once, reading ``.env`` from the repository root."""
    root = find_repo_root()
    return Settings(_env_file=root / ".env")  # type: ignore[call-arg]
