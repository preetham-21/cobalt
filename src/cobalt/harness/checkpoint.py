"""Agent checkpoints: resume a task after a crash or kill.

A checkpoint is written atomically (write temp file, then rename) after every
completed step, i.e. when the conversation ends with the tool results of the
latest assistant turn. Resuming replays nothing: the loop continues from the
saved messages, step counter and spend.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Checkpoint:
    """Saved state of one agent task."""

    run_id: str
    task_id: str
    agent: str
    step: int
    messages: list[dict[str, Any]]
    usage: dict[str, Any]
    elapsed_s: float
    status: str = "running"  # running | finished
    state: dict[str, Any] = field(default_factory=dict)  # loop bookkeeping

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class CheckpointStore:
    """Checkpoints under ``runs/checkpoints/<run_id>/<task_id>.json``."""

    def __init__(self, runs_dir: Path) -> None:
        self.root = runs_dir / "checkpoints"

    def path(self, run_id: str, task_id: str) -> Path:
        return self.root / run_id / f"{task_id}.json"

    def save(self, cp: Checkpoint) -> Path:
        path = self.path(cp.run_id, cp.task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cp.to_dict(), default=str), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def load(self, run_id: str, task_id: str) -> Checkpoint | None:
        path = self.path(run_id, task_id)
        if not path.is_file():
            return None
        return Checkpoint(**json.loads(path.read_text(encoding="utf-8")))

    def delete(self, run_id: str, task_id: str) -> None:
        self.path(run_id, task_id).unlink(missing_ok=True)
