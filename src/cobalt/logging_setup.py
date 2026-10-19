"""Structured logging: JSON lines to ``runs/logs/cobalt.jsonl``, terse console.

Every event passes through :func:`make_redactor`, which masks secret-looking
keys and scrubs known secret values out of any string field, so the API key
cannot reach a log file even by accident.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterable, MutableMapping
from typing import Any

import structlog

from cobalt.config import Settings

LOG_FILE_NAME = "cobalt.jsonl"
REDACTED = "***REDACTED***"
_SECRET_KEY = re.compile(r"(api[_-]?key|secret|token|password|authorization)", re.IGNORECASE)

EventDict = MutableMapping[str, Any]


def make_redactor(
    secrets: Iterable[str],
) -> structlog.typing.Processor:
    """Build a structlog processor that removes secrets from an event dict."""
    values = [s for s in secrets if s]

    def scrub(value: Any) -> Any:
        if isinstance(value, str):
            for secret in values:
                value = value.replace(secret, REDACTED)
            return value
        if isinstance(value, dict):
            return {
                k: REDACTED if _SECRET_KEY.search(str(k)) else scrub(v) for k, v in value.items()
            }
        if isinstance(value, list | tuple):
            return type(value)(scrub(v) for v in value)
        return value

    def redact(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
        for key in list(event_dict):
            if _SECRET_KEY.search(key):
                event_dict[key] = REDACTED
            else:
                event_dict[key] = scrub(event_dict[key])
        return event_dict

    return redact


def configure_logging(settings: Settings, *, console_level: str = "WARNING") -> None:
    """Route structlog through stdlib logging to a JSON file and stderr.

    Safe to call more than once; handlers are replaced, not duplicated.
    """
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        make_redactor(settings.secret_values()),
    ]

    file_handler = logging.FileHandler(settings.logs_dir / LOG_FILE_NAME, encoding="utf-8")
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(sort_keys=True),
            ],
        )
    )
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=False),
            ],
        )
    )

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.setLevel(settings.log_level.upper())

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a named structlog logger."""
    return structlog.get_logger(name)
