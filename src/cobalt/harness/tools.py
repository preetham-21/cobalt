"""Typed tool registry.

A tool is a name, a description, a pydantic input model and a handler. The
registry renders the API ``tools`` list (strict JSON schemas), validates
every call before running it, and turns any failure into an ``is_error``
tool result - a tool can never crash the agent loop.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

MAX_RESULT_CHARS = 16_000


class ToolInput(BaseModel):
    """Base class for tool input models (no extra fields allowed)."""

    model_config = ConfigDict(extra="forbid")


class ToolError(Exception):
    """Raised by a handler for an expected, explainable failure."""


@dataclass
class ToolOutcome:
    """Result of one tool call as seen by the loop and the tracer."""

    tool_use_id: str
    name: str
    content: str
    is_error: bool
    duration_s: float
    error_kind: str | None = None  # invalid_input | unknown_tool | tool_error | crash
    data: dict[str, Any] | None = None  # structured result for the harness (not sent)
    terminal: bool = False  # the tool ends the agent run (e.g. finish)

    def to_block(self) -> dict[str, Any]:
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": self.content,
        }
        if self.is_error:
            block["is_error"] = True
        return block


@dataclass
class ToolReply:
    """What a handler returns: text for the model, optional structured data."""

    text: str
    data: dict[str, Any] | None = None
    terminal: bool = False


Handler = Callable[[Any], "ToolReply | str"]


@dataclass
class Tool:
    """One tool the model may call."""

    name: str
    description: str
    input_model: type[ToolInput]
    handler: Handler

    def schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        _strictify(schema)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
            "strict": True,
        }


def _strictify(schema: dict[str, Any]) -> None:
    """Strict tool use needs additionalProperties=false and every property
    required at every object level."""
    if schema.get("type") == "object" or "properties" in schema:
        schema["additionalProperties"] = False
        schema["required"] = sorted(schema.get("properties", {}))
    for key in ("properties", "$defs"):
        for sub in schema.get(key, {}).values():
            if isinstance(sub, dict):
                _strictify(sub)
    items = schema.get("items")
    if isinstance(items, dict):
        _strictify(items)
    for sub in schema.get("anyOf", []):
        if isinstance(sub, dict):
            _strictify(sub)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
        if "default" in prop:
            prop.pop("default")


def truncate(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    """Keep head and tail of long output, marking the cut."""
    if len(text) <= limit:
        return text
    head = text[: limit * 2 // 3]
    tail = text[-limit // 4 :]
    return f"{head}\n... [{len(text) - len(head) - len(tail)} characters omitted] ...\n{tail}"


class ToolRegistry:
    """A named set of tools."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool

    def subset(self, names: Iterable[str]) -> ToolRegistry:
        """A registry with only ``names`` (in that order)."""
        return ToolRegistry(self._tools[n] for n in names)

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        """API tool definitions, in a stable order (cache-friendly)."""
        return [self._tools[n].schema() for n in sorted(self._tools)]

    def call(self, tool_use_id: str, name: str, raw_input: Any) -> ToolOutcome:
        start = time.monotonic()

        def outcome(
            content: str, is_error: bool, kind: str | None = None, reply: ToolReply | None = None
        ) -> ToolOutcome:
            return ToolOutcome(
                tool_use_id,
                name,
                truncate(content),
                is_error,
                time.monotonic() - start,
                kind,
                reply.data if reply else None,
                reply.terminal if reply else False,
            )

        tool = self._tools.get(name)
        if tool is None:
            return outcome(
                f"Unknown tool {name!r}. Available: {', '.join(self.names)}", True, "unknown_tool"
            )
        try:
            args = tool.input_model.model_validate(raw_input or {})
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or '(input)'}: {e['msg']}"
                for e in exc.errors()
            )
            return outcome(f"Invalid input for {name}: {problems}", True, "invalid_input")
        try:
            reply = tool.handler(args)
        except ToolError as exc:
            return outcome(f"Error: {exc}", True, "tool_error")
        except Exception as exc:
            return outcome(f"Internal error in {name}: {type(exc).__name__}: {exc}", True, "crash")
        if isinstance(reply, str):
            reply = ToolReply(reply)
        return outcome(reply.text, False, None, reply)
