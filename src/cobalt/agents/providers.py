"""Choose the LLM client for a run."""

from __future__ import annotations

from pathlib import Path

from cobalt.config import Settings
from cobalt.harness.llm import AnthropicClient, LLMClient
from cobalt.harness.replay import replay_client

PROVIDERS = ("anthropic", "replay")


def make_client(settings: Settings, provider: str, program: str, corpus: str) -> LLMClient:
    """``anthropic`` = the real model; ``replay`` = scripted reference migration.

    The Anthropic SDK resolves credentials itself (``ANTHROPIC_API_KEY``, an
    auth token, or an ``ant auth login`` profile), so a missing key in
    ``.env`` is not treated as fatal here; the first call reports it.
    """
    if provider == "anthropic":
        key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
        return AnthropicClient(api_key=key)
    if provider == "replay":
        return replay_client(program, Path(settings.root) / "baselines" / corpus)
    raise ValueError(f"unknown provider {provider!r}; choose from {', '.join(PROVIDERS)}")
