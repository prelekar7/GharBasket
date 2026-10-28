"""Build the app's routes once per process instead of once per test.

`create_app` is half the suite's CPU: almost every test makes an app, and each app re-analyses the
parameters of its 300 routes (FastAPI's `get_dependant`: a pydantic TypeAdapter per parameter, about
1,300 per app, 0.2 s on a developer machine and a second with coverage on). The routes do not depend
on the settings, so this keeps what `get_dependant` found for a (path, function) and hands each
caller a copy of it, as FastAPI itself would build anew; the only thing FastAPI changes in a
dependant after building it is the list of its dependencies (route-level `dependencies=[...]`), so
that list is copied.

Test-only: the application code is untouched. If a FastAPI upgrade changes `get_dependant`'s
parameters, nothing is patched and the suite is just slower.
"""

import copy
import inspect
from collections.abc import Callable
from typing import Any

from fastapi import routing
from fastapi.dependencies import models, utils

EXPECTED = {
    "path",
    "call",
    "name",
    "own_oauth_scopes",
    "parent_oauth_scopes",
    "use_cache",
    "scope",
}

_found: dict[tuple[object, ...], models.Dependant] = {}


def _scopes(scopes: list[str] | None) -> tuple[str, ...] | None:
    return None if scopes is None else tuple(scopes)


def install() -> None:
    real: Callable[..., models.Dependant] = utils.get_dependant
    if (
        getattr(real, "kept_dependants", False)
        or set(inspect.signature(real).parameters) != EXPECTED
    ):
        return

    def get_dependant(**kwargs: Any) -> models.Dependant:
        try:
            key = (
                kwargs["path"],
                kwargs["call"],
                kwargs.get("name"),
                _scopes(kwargs.get("own_oauth_scopes")),
                _scopes(kwargs.get("parent_oauth_scopes")),
                kwargs.get("use_cache", True),
                kwargs.get("scope"),
            )
            kept = _found.get(key)
        except TypeError:  # a callable that cannot be hashed
            return real(**kwargs)
        if kept is None:
            kept = _found[key] = real(**kwargs)
        made = copy.copy(kept)
        made.dependencies = list(kept.dependencies)
        return made

    get_dependant.kept_dependants = True  # pyright: ignore[reportFunctionMemberAccess]
    utils.get_dependant = get_dependant
    routing.get_dependant = get_dependant  # pyright: ignore[reportPrivateImportUsage]
