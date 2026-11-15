"""Structured logging: one JSON object per line on stdout, carrying the request context.

Both our own `structlog` loggers and standard-library loggers (uvicorn, libraries) go through the
same pipeline, so every line has the same shape.

Every line is scrubbed last: phone numbers, e-mail addresses, tokens and database passwords are
masked even when they arrive inside an exception message or a library's log text (DPDP: logs are
not a place for PII). Names and addresses have no pattern to find in free text, so they are masked
by field name instead (`PERSONAL_FIELDS`), at any depth of the line.
"""

import logging
import re
import sys
from collections.abc import Callable
from typing import Any

import structlog
from opentelemetry import trace
from structlog.typing import EventDict, Processor, WrappedLogger


def _mask_phone(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group())
    return f"[phone ••{digits[-2:]}]"


_SCRUBBERS: tuple[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]], ...] = (
    # JSON Web Tokens (our access tokens, Firebase ID tokens).
    (re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"), "[jwt]"),
    # Authorization headers and other bearer secrets.
    (re.compile(r"(?i)\bbearer(?:\s+|%20)[\w.~+/=%-]+"), "Bearer [redacted]"),
    # Basic auth (OTEL_EXPORTER_OTLP_HEADERS carries one, percent-encoded or not).
    (re.compile(r"(?i)\bbasic(?:\s+|%20)[\w.~+/=%-]+"), "Basic [redacted]"),
    # Passwords inside connection strings: scheme://user:password@host
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@\s]+@"), r"\1[redacted]@"),
    # E-mail addresses; not the user of a connection string (scheme://user@host).
    (re.compile(r"(?<![\w.+/-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    # Indian mobile numbers: 10 digits from 6-9, optional +91/91, spaces or dashes anywhere.
    # Keeps the last two digits so support can still tell numbers apart.
    (re.compile(r"(?<![\w+])(?:\+?91[\s-]?)?[6-9](?:[\s-]?\d){9}(?![\w])"), _mask_phone),
)


_NAME, _ADDRESS = "[name]", "[address]"
# Fields that hold a person's name or where they live (handbook 13 §13.5), as our models and
# payloads call them: masked whatever their value, at any depth of a log line.
PERSONAL_FIELDS: dict[str, str] = {
    **dict.fromkeys(
        (
            "full_name",
            "display_name",
            "first_name",
            "last_name",
            "contact_name",
            "customer_name",
            "member_name",
            "taken_by_name",
        ),
        _NAME,
    ),
    **dict.fromkeys(
        (
            "address",
            "address_line",
            "line1",
            "line2",
            "flat",
            "flat_label",
            "flat_no",
            "landmark",
            "street",
        ),
        _ADDRESS,
    ),
    "email": "[email]",
}
# A bare `name` is masked only inside logged data (a payload, `details`, a dict of headers), where
# it may be a household member's. A top-level field of a log line is chosen at the call site,
# where `name` names code (a job, a probe, a model); masking it would hide what ran, and reviewers
# keep people's names out of call sites (handbook 11 §11.7).
_NESTED_FIELDS = {**PERSONAL_FIELDS, "name": _NAME}


def scrub(text: str) -> str:
    for pattern, replacement in _SCRUBBERS:
        text = pattern.sub(replacement, text)
    return text


def _field(key: object, value: Any, fields: dict[str, str]) -> Any:
    label = fields.get(key.lower()) if isinstance(key, str) else None
    if label is not None and value is not None and value != "":
        return label  # an empty value stays visible: "no name given" is not personal
    return _scrub_value(value)


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {key: _field(key, item, _NESTED_FIELDS) for key, item in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list | tuple):
        return type(value)(_scrub_value(item) for item in value)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    return value


def scrub_sensitive(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    return {key: _field(key, value, PERSONAL_FIELDS) for key, value in event_dict.items()}


def add_trace_ids(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """`trace_id` and `span_id` of the span the line is written in (M0 §6.2), so a Render log line
    leads to its trace and back; nothing outside a span or in one that is not sampled (no trace
    to lead to)."""
    context = trace.get_current_span().get_span_context()
    if context.is_valid and context.trace_flags.sampled:
        event_dict["trace_id"] = f"{context.trace_id:032x}"
        event_dict["span_id"] = f"{context.span_id:016x}"
    return event_dict


def configure_logging(level: str = "INFO", *, json: bool = True) -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        add_trace_ids,
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
