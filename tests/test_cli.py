from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cobalt import __version__
from cobalt.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_inventory_json(tmp_path: Path) -> None:
    out = tmp_path / "inv.json"
    result = runner.invoke(app, ["inventory", "--corpus", "synthetic", "--json", "--out", str(out)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.stdout)
    assert printed == json.loads(out.read_text(encoding="utf-8"))
    assert len(printed["programs"]) == 5


def test_inventory_tables(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["inventory", "--corpus", "synthetic", "--out", str(tmp_path / "i.json")]
    )
    assert result.exit_code == 0, result.output
    assert "PAYROLL" in result.stdout
    assert "programs by scope: now=5" in result.stdout


def test_inventory_unknown_corpus(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["inventory", "--corpus", "nope", "--out", str(tmp_path / "i.json")]
    )
    assert result.exit_code == 1
    assert "corpus not found" in result.output
