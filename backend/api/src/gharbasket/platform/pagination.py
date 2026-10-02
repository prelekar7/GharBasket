"""Cursor pagination (TDD §4.1): `?cursor=&limit=` in, `{items, next_cursor}` out.

The cursor is an opaque, URL-safe token holding the sort key of the last item returned, so pages
stay stable while rows are added (unlike offset paging). Clients never parse it.
"""

import base64
import binascii
import json
from typing import Annotated, Any

from fastapi import Query
from pydantic import BaseModel

from gharbasket.platform.errors import ProblemError

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None


def encode_cursor(position: dict[str, Any]) -> str:
    raw = json.dumps(position, separators=(",", ":"), default=str).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        position = json.loads(raw)
    except (binascii.Error, ValueError) as exc:
        raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from exc
    if not isinstance(position, dict):
        raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.")
    return position  # pyright: ignore[reportUnknownVariableType]


Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT, description="Items per page")]
Cursor = Annotated[
    str | None, Query(max_length=512, description="`next_cursor` from the last page")
]
