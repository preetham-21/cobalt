from __future__ import annotations

from pathlib import Path

import pytest

from cobalt.config import get_settings

ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC = ROOT / "corpus" / "synthetic"


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Point settings at the repo, keep logs out of runs/, and never pick up
    a developer's real .env or API key."""
    monkeypatch.setenv("COBALT_ROOT", str(ROOT))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("COBALT_MODEL", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(
        "cobalt.config.Settings.logs_dir", property(lambda _self: tmp_path / "logs")
    )
    yield
    get_settings.cache_clear()
