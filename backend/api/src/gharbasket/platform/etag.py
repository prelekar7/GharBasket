"""Optimistic locking (handbook 10): reads send `ETag: "<version>"`, edits return it in `If-Match`.

    @router.patch("/households/{household_id}")
    async def edit(..., expected: IfMatch, response: Response) -> HouseholdOut:
        if household.version != expected:
            raise ProblemError(412, "identity.version_conflict", "...")
        ...
        set_etag(response, household.version)

A missing `If-Match` is 428 `request.precondition_required`. An unreadable one never matches, so
the module answers 412 with its own code and the client reloads.
"""

from typing import Annotated

from fastapi import Depends, Header, Response

from gharbasket.platform.errors import ProblemError

# What an unreadable If-Match becomes: versions start at 1, so it never matches.
NO_VERSION = 0


def etag(version: int) -> str:
    return f'"{version}"'


def set_etag(response: Response, version: int) -> None:
    response.headers["ETag"] = etag(version)


async def if_match(
    value: Annotated[str | None, Header(alias="If-Match", max_length=64)] = None,
) -> int:
    if value is None:
        raise ProblemError(
            428,
            "request.precondition_required",
            'Send If-Match with the ETag from your last read, e.g. If-Match: "3".',
        )
    tag = value.strip().removeprefix("W/").strip('"')
    return int(tag) if tag.isdigit() else NO_VERSION


IfMatch = Annotated[int, Depends(if_match)]
