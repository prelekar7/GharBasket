"""Structured logging: one JSON object per line on stdout, carrying the request context.

Both our own `structlog` loggers and standard-library loggers (uvicorn, libraries) go through the
same pipeline, so every line has the same shape.

Every line is scrubbed last: phone numbers, tokens and database passwords are masked even when they
arrive inside an exception message or a library's log text (DPDP: logs are not a place for PII).
"""

import logging
import re
import sys
from collections.abc import Callable
from typing import Any

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger


def _mask_phone(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group())
    return f"[phone ••{digits[-2:]}]"


_SCRUBBERS: tuple[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]], ...] = (
    # JSON Web Tokens (our access tokens, Firebase ID tokens).
    (re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"), "[jwt]"),
    # Authorization headers and other bearer secrets.
    (re.compile(r"(?i)\bbearer\s+[\w.~+/=-]+"), "Bearer [redacted]"),
    # Passwords inside connection strings: scheme://user:password@host
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@\s]+@"), r"\1[redacted]@"),
    # Indian mobile numbers: 10 digits from 6-9, optional +91/91, spaces or dashes anywhere.
    # Keeps the last two digits so support can still tell numbers apart.
    (re.compile(r"(?<![\w+])(?:\+?91[\s-]?)?[6-9](?:[\s-]?\d){9}(?![\w])"), _mask_phone),
)


def scrub(text: str) -> str:
    for pattern, replacement in _SCRUBBERS:
        text = pattern.sub(replacement, text)
    return text


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {key: _scrub_value(item) for key, item in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list | tuple):
        return type(value)(_scrub_value(item) for item in value)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    return value


def scrub_sensitive(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    return {key: _scrub_value(value) for key, value in event_dict.items()}


def configure_logging(level: str = "INFO", *, json: bool = True) -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    # Scrubbing runs after exceptions are turned into text, so tracebacks are masked too.
    renderers: list[Processor] = (
        [structlog.processors.format_exc_info, scrub_sensitive, structlog.processors.JSONRenderer()]
        if json
        else [
            structlog.processors.format_exc_info,
            scrub_sensitive,
            structlog.dev.ConsoleRenderer(),
        ]
    )

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *renderers],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    # Requests are logged by RequestContextMiddleware with the request id and duration.
    logging.getLogger("uvicorn.access").disabled = True
