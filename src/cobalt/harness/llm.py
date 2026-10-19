"""LLM clients for the agent loop.

The loop talks to an :class:`LLMClient` and receives a normalised
:class:`LLMResponse` whose ``content`` is a list of plain dicts exactly as
the API returned them. Plain dicts keep the conversation JSON-serialisable
for checkpoints, and they are sent back unchanged, which preserved thinking
on current models requires (the harness never edits earlier turns).

Two implementations:

* :class:`AnthropicClient` - the real Messages API (streaming, prompt
  caching, explicit effort, server-side refusal fallback).
* :class:`ScriptedClient` - replays prepared responses; used by tests and by
  the offline ``replay`` demo. It is labelled as such in every trace.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

#: USD per million tokens: (input, output, cache write, cache read).
#: Cache writes are 1.25x input (5-minute TTL); reads as published per model.
PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-fable-5-1": (10.00, 50.00, 12.50, 0.25),
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 6.25, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 2.50, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "scripted": (0.0, 0.0, 0.0, 0.0),
}
DEFAULT_PRICE_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class Usage:
    """Token usage of one or more calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cost_usd += other.cost_usd

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_write_tokens + self.cache_read_tokens

    def to_dict(self) -> dict[str, float]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Usage:
        return cls(**{k: data.get(k, 0) for k in cls.__dataclass_fields__})


def cost_usd(
    model: str, input_tokens: int, output_tokens: int, cache_write: int = 0, cache_read: int = 0
) -> float:
    """Dollar cost of one call at list prices."""
    price = PRICING.get(model) or PRICING[DEFAULT_PRICE_MODEL]
    return (
        input_tokens * price[0]
        + output_tokens * price[1]
        + cache_write * price[2]
        + cache_read * price[3]
    ) / 1_000_000


@dataclass
class LLMResponse:
    """One model turn, provider-neutral."""

    content: list[dict[str, Any]]
    stop_reason: str
    model: str
    usage: Usage
    response_id: str = ""
    duration_s: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    @property
    def text(self) -> str:
        return "\n".join(b.get("text", "") for b in self.content if b.get("type") == "text")


@dataclass(frozen=True)
class LLMRequest:
    """Everything sent for one turn."""

    model: str
    system: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    max_tokens: int = 32000
    effort: str = "high"


class LLMClient(Protocol):
    """Anything that can produce the next assistant turn."""

    name: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...


class LLMError(RuntimeError):
    """The provider failed in a way the loop should record and stop on."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class AnthropicClient:
    """Claude via the official SDK (streaming, cached, with refusal fallback)."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        max_retries: int = 4,
        timeout_s: float = 900.0,
        use_fallbacks: bool = True,
    ) -> None:
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(
            api_key=api_key, max_retries=max_retries, timeout=timeout_s
        )
        self.use_fallbacks = use_fallbacks

    def complete(self, request: LLMRequest) -> LLMResponse:
        anthropic = self._anthropic
        tools = [{**t, "eager_input_streaming": True} for t in request.tools]
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": request.messages,
            "tools": tools,
            "output_config": {"effort": request.effort},
            # caches the longest stable prefix (tools + system + history)
            "cache_control": {"type": "ephemeral"},
        }
        if self.use_fallbacks:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        start = time.monotonic()
        try:
            with self._client.beta.messages.stream(**params) as stream:
                message = stream.get_final_message()
        except anthropic.RateLimitError as exc:
            raise LLMError(f"rate limited: {exc}", retryable=True) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"connection error: {exc}", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(
                f"API error {exc.status_code}: {exc.message}", retryable=exc.status_code >= 500
            ) from exc
        data = message.to_dict()
        u = data.get("usage") or {}
        model = data.get("model") or request.model
        usage = Usage(
            input_tokens=u.get("input_tokens") or 0,
            output_tokens=u.get("output_tokens") or 0,
            cache_write_tokens=u.get("cache_creation_input_tokens") or 0,
            cache_read_tokens=u.get("cache_read_input_tokens") or 0,
        )
        usage.cost_usd = cost_usd(
            model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_write_tokens,
            usage.cache_read_tokens,
        )
        extra = {}
        if data.get("stop_details"):
            extra["stop_details"] = data["stop_details"]
        return LLMResponse(
            content=data.get("content", []),
            stop_reason=data.get("stop_reason") or "end_turn",
            model=model,
            usage=usage,
            response_id=data.get("id", ""),
            duration_s=time.monotonic() - start,
            extra=extra,
        )


Responder = Callable[[LLMRequest], LLMResponse]


class ScriptedClient:
    """Replays a fixed sequence of responses (or computes them from the request).

    Token counts are estimated from text length so budgets and traces behave
    realistically; cost is reported as zero.
    """

    name = "scripted"

    def __init__(self, script: Sequence[LLMResponse | Responder]) -> None:
        self._script = list(script)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._script:
            return scripted_text("(script exhausted)")
        item = self._script.pop(0)
        response = item(request) if callable(item) else item
        if not response.usage.input_tokens:
            prompt_chars = len(request.system) + sum(len(str(m)) for m in request.messages)
            response.usage.input_tokens = prompt_chars // 4
            response.usage.output_tokens = max(1, len(str(response.content)) // 4)
        return response


def scripted_tool_call(
    name: str, tool_input: dict[str, Any], *, text: str = "", call_id: str | None = None
) -> LLMResponse:
    """A scripted assistant turn that calls one tool."""
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    digest = hashlib.sha256(json.dumps([name, tool_input], sort_keys=True).encode()).hexdigest()
    content.append(
        {
            "type": "tool_use",
            "id": call_id or f"toolu_{digest[:16]}",
            "name": name,
            "input": tool_input,
        }
    )
    return LLMResponse(content, "tool_use", "scripted", Usage())


def scripted_text(text: str, stop_reason: str = "end_turn") -> LLMResponse:
    """A scripted assistant turn with text only."""
    return LLMResponse([{"type": "text", "text": text}], stop_reason, "scripted", Usage())
