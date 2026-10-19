"""Context management: watch the window, start a fresh episode when it is full.

Current models bind thinking to the exact conversation, so the harness never
edits earlier turns. When the prompt grows past the budget, the loop instead
starts a new *episode*: a single user message holding the original task plus
a folded digest of the previous episode (what was written, compiled and
verified, and the latest equivalence feedback). The workspace keeps every
file, so nothing is lost - the agent re-reads what it needs.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

DEFAULT_THRESHOLD_TOKENS = 150_000


def _blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def composition(messages: list[dict[str, Any]]) -> dict[str, int]:
    """Characters in the window by category (task, assistant text, tool inputs,
    tool results per tool)."""
    names: dict[str, str] = {}
    out: Counter[str] = Counter()
    for i, msg in enumerate(messages):
        for block in _blocks(msg):
            kind = block.get("type")
            if kind == "tool_use":
                names[block.get("id", "")] = block.get("name", "?")
                out["tool_inputs"] += len(json.dumps(block.get("input", {})))
            elif kind == "tool_result":
                tool = names.get(block.get("tool_use_id", ""), "?")
                content = block.get("content", "")
                out[f"result:{tool}"] += len(
                    content if isinstance(content, str) else json.dumps(content)
                )
            elif kind == "text":
                key = (
                    "task" if i == 0 else ("assistant" if msg["role"] == "assistant" else "harness")
                )
                out[key] += len(block.get("text", ""))
            elif kind == "thinking":
                out["thinking"] += len(block.get("thinking", ""))
    return dict(out)


def digest(messages: list[dict[str, Any]]) -> str:
    """Folded record of an episode: actions, files, latest results."""
    names: dict[str, str] = {}
    inputs: dict[str, dict[str, Any]] = {}
    calls: Counter[str] = Counter()
    written: list[str] = []
    last: dict[str, str] = {}
    last_text = ""
    for msg in messages:
        for block in _blocks(msg):
            kind = block.get("type")
            if kind == "tool_use":
                tid = block.get("id", "")
                names[tid] = block.get("name", "?")
                inputs[tid] = block.get("input") or {}
                calls[names[tid]] += 1
                if names[tid] == "write_file":
                    path = inputs[tid].get("path", "?")
                    if path not in written:
                        written.append(path)
            elif kind == "tool_result":
                tool = names.get(block.get("tool_use_id", ""), "?")
                content = block.get("content", "")
                text = content if isinstance(content, str) else json.dumps(content)
                if tool in ("compile_java", "run_equivalence", "capture_golden"):
                    last[tool] = ("ERROR " if block.get("is_error") else "") + text
            elif kind == "text" and msg["role"] == "assistant" and block.get("text"):
                last_text = block["text"]
    lines = ["## Progress from your previous context (folded to save space)"]
    lines.append("Tool calls so far: " + ", ".join(f"{k} x{v}" for k, v in calls.most_common()))
    if written:
        lines.append(
            "Files you wrote (still in the workspace - read_file to see them): "
            + ", ".join(written)
        )
    for tool in ("compile_java", "run_equivalence", "capture_golden"):
        if tool in last:
            lines.append(f"Latest {tool} result:\n{last[tool][-3000:]}")
    if last_text:
        lines.append(f"Your last note:\n{last_text[-1200:]}")
    lines.append("Continue from here; do not redo work that is already done.")
    return "\n\n".join(lines)


class EpisodeCompactor:
    """:class:`~cobalt.harness.loop.ContextPolicy` that folds the window into
    a new episode once the prompt exceeds ``threshold_tokens``."""

    def __init__(self, threshold_tokens: int = DEFAULT_THRESHOLD_TOKENS) -> None:
        self.threshold_tokens = threshold_tokens
        self.last_composition: dict[str, int] = {}

    def maybe_new_episode(
        self, messages: list[dict[str, Any]], last_input_tokens: int, task: str
    ) -> list[dict[str, Any]] | None:
        self.last_composition = composition(messages)
        if last_input_tokens < self.threshold_tokens or len(messages) < 3:
            return None
        return [{"role": "user", "content": f"{task}\n\n{digest(messages)}"}]
