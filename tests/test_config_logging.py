from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from cobalt.config import Settings, find_repo_root, get_settings
from cobalt.logging_setup import REDACTED, configure_logging, get_logger, make_redactor
from conftest import ROOT

FAKE_KEY = "sk-ant-test-0123456789abcdef"


def test_repo_root_from_env() -> None:
    assert find_repo_root() == ROOT


def test_repo_root_walks_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COBALT_ROOT")
    assert find_repo_root(ROOT / "src" / "cobalt") == ROOT


def test_defaults_and_derived_paths(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)
    assert settings.model == "claude-opus-5-5"
    assert settings.anthropic_api_key is None
    assert settings.corpus_dir == ROOT / "corpus"
    assert settings.workspace_dir == ROOT / "workspace"


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COBALT_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    settings = Settings(_env_file=None)
    assert settings.model == "claude-sonnet-5-5"
    assert settings.secret_values() == [FAKE_KEY]


def test_api_key_never_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    settings = Settings(_env_file=None)
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings)
    assert FAKE_KEY not in settings.model_dump_json()


def test_env_file_is_read(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(f"ANTHROPIC_API_KEY={FAKE_KEY}\nCOBALT_LOG_LEVEL=DEBUG\n", encoding="utf-8")
    settings = Settings(_env_file=env)
    assert settings.log_level == "DEBUG"
    assert settings.secret_values() == [FAKE_KEY]


def test_redactor_masks_keys_and_values() -> None:
    redact = make_redactor([FAKE_KEY])
    event = {
        "event": f"calling api with {FAKE_KEY}",
        "api_key": "anything",
        "headers": {"Authorization": "Bearer x", "accept": "json"},
        "args": [FAKE_KEY, 3],
        "count": 7,
    }
    out = redact(None, "info", event)
    assert out["event"] == f"calling api with {REDACTED}"
    assert out["api_key"] == REDACTED
    assert out["headers"] == {"Authorization": REDACTED, "accept": "json"}
    assert out["args"] == [REDACTED, 3]
    assert out["count"] == 7


def test_logs_are_json_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    get_settings.cache_clear()
    settings = get_settings()
    configure_logging(settings)
    get_logger("test").warning("leak.attempt", detail=f"key={FAKE_KEY}", token="t")
    logging.shutdown()

    text = (settings.logs_dir / "cobalt.jsonl").read_text(encoding="utf-8")
    assert FAKE_KEY not in text
    record = json.loads(text.strip().splitlines()[-1])
    assert record["event"] == "leak.attempt"
    assert record["level"] == "warning"
    assert record["token"] == REDACTED
    assert record["detail"] == f"key={REDACTED}"
