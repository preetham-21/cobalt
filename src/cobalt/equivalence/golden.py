"""Golden outputs: what the original COBOL produced for each test case.

Layout (one directory per program and case)::

    <golden_root>/<PROGRAM>/<case>/
        meta.json        program, case, return code, input/source digests
        stdout.txt       DISPLAY output
        out/<LOGICAL>    every declared output file ("missing" listed in meta)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from cobalt.equivalence.runtime import RunOutput
from cobalt.equivalence.spec import ProgramSpec, TestCase


def _sha(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def source_digest(prog: ProgramSpec, copybooks: list[Path]) -> str:
    return _sha([*prog.sources, *sorted(copybooks)])


def input_digest(case: TestCase) -> str:
    return _sha([case.inputs[k] for k in sorted(case.inputs)])


@dataclass
class Golden:
    """A stored golden result."""

    meta: dict
    output: RunOutput


class GoldenStore:
    """Read and write golden outputs under one root."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def case_dir(self, program: str, case: str) -> Path:
        return self.root / program / case

    def save(
        self,
        prog: ProgramSpec,
        case: TestCase,
        output: RunOutput,
        *,
        source_sha: str,
        toolchain: str,
    ) -> Path:
        d = self.case_dir(prog.name, case.name)
        (d / "out").mkdir(parents=True, exist_ok=True)
        for old in (d / "out").iterdir():
            old.unlink()
        missing = []
        for logical, data in output.files.items():
            if data is None:
                missing.append(logical)
            else:
                (d / "out" / logical).write_bytes(data)
        (d / "stdout.txt").write_bytes(output.stdout.encode("utf-8"))
        meta = {
            "program": prog.name,
            "case": case.name,
            "returncode": output.returncode,
            "outputs": list(prog.outputs),
            "missing": missing,
            "stdout_compared": prog.stdout,
            "source_sha": source_sha,
            "input_sha": input_digest(case),
            "toolchain": toolchain,
        }
        (d / "meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        return d

    def load(self, program: str, case: str) -> Golden | None:
        d = self.case_dir(program, case)
        meta_path = d / "meta.json"
        if not meta_path.is_file():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        files: dict[str, bytes | None] = {}
        for logical in meta["outputs"]:
            path = d / "out" / logical
            files[logical] = path.read_bytes() if path.is_file() else None
        stdout = (d / "stdout.txt").read_bytes().decode("utf-8")
        return Golden(meta, RunOutput(meta["returncode"], stdout, "", files, 0.0))

    def is_fresh(self, prog: ProgramSpec, case: TestCase, source_sha: str) -> bool:
        g = self.load(prog.name, case.name)
        return (
            g is not None
            and g.meta.get("source_sha") == source_sha
            and g.meta.get("input_sha") == input_digest(case)
        )


class LayeredGoldenStore(GoldenStore):
    """Reads from several stores in order; writes to the first.

    Lets a migration run add agent-written test cases (golden in the run's
    workspace) on top of the committed corpus golden outputs.
    """

    def __init__(self, stores: list[GoldenStore]) -> None:
        super().__init__(stores[0].root)
        self.stores = stores

    def load(self, program: str, case: str) -> Golden | None:
        for store in self.stores:
            found = GoldenStore.load(store, program, case)
            if found is not None:
                return found
        return None

    def is_fresh(self, prog: ProgramSpec, case: TestCase, source_sha: str) -> bool:
        return any(GoldenStore.is_fresh(s, prog, case, source_sha) for s in self.stores)
