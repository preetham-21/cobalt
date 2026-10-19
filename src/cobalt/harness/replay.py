"""Offline ``replay`` provider: a scripted agent that re-enacts a migration.

It drives the *real* harness - tools, sandbox, compilation, equivalence,
budgets, traces, checkpoints - with tool calls taken from the hand-written
reference migrations in ``baselines/<corpus>``. No model is involved and no
claim about model quality can be drawn from it: it exists so the whole
pipeline can be demonstrated and tested without an API key. Every trace it
produces names the provider ``scripted``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cobalt.equivalence.runtime import java_package_dir
from cobalt.harness.llm import LLMRequest, LLMResponse, ScriptedClient, scripted_tool_call

JAVA_SRC = Path("src/main/java")


def _has_tool(request: LLMRequest, name: str) -> bool:
    return any(t["name"] == name for t in request.tools)


def replay_script(
    program: str, baselines: Path, *, mutate: dict[str, tuple[str, str]] | None = None
) -> list[Any]:
    """Tool calls that migrate ``program`` by writing its baseline Java.

    ``mutate`` maps a file name to (old, new) text replaced in the *first*
    write, followed by a corrected write after the failing equivalence run -
    used to exercise the diagnose-and-repair path.
    """
    pkg = baselines / JAVA_SRC / java_package_dir(program)
    files = sorted(pkg.rglob("*.java")) if pkg.is_dir() else []

    def write(path: Path, content: str) -> LLMResponse:
        rel = path.relative_to(baselines).as_posix()
        return scripted_tool_call(
            "write_file", {"path": rel, "content": content}, text=f"Writing {path.name}."
        )

    script: list[Any] = [
        scripted_tool_call(
            "read_cobol", {"name": program, "line_numbers": True}, text=f"Reading {program}."
        ),
        lambda req: (
            scripted_tool_call("query_graph", {"kind": "context", "name": program})
            if _has_tool(req, "query_graph")
            else scripted_tool_call("list_files", {"directory": ""})
        ),
    ]
    for f in files:
        content = f.read_text(encoding="utf-8")
        if mutate and f.name in mutate:
            old, new = mutate[f.name]
            script.append(write(f, content.replace(old, new)))
        else:
            script.append(write(f, content))
    script += [
        scripted_tool_call("compile_java", {}),
        scripted_tool_call("run_equivalence", {"case": None}),
    ]
    if mutate:
        for f in files:
            if f.name in mutate:
                script.append(write(f, f.read_text(encoding="utf-8")))
        script += [
            scripted_tool_call("compile_java", {}, call_id="toolu_recompile"),
            scripted_tool_call("run_equivalence", {"case": None}, call_id="toolu_rerun"),
        ]
    script.append(
        scripted_tool_call(
            "finish", {"success": True, "summary": f"Replayed reference migration of {program}."}
        )
    )
    return script


def replay_client(program: str, baselines: Path, **kwargs: Any) -> ScriptedClient:
    """A :class:`ScriptedClient` that re-enacts ``program``'s baseline migration."""
    return ScriptedClient(replay_script(program, baselines, **kwargs))
