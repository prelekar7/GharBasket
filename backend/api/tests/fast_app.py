"""Build the app's routes once per process instead of once per test.

Making an app is half the suite's CPU: almost every test makes one, and each app analyses its 300
routes again (a pydantic TypeAdapter per parameter and per response model, about 1,700 per app: a
fifth of a second on a developer machine, a second with coverage on). The routes do not depend on
the settings, so this keeps what FastAPI found for a route's parameters (`get_dependant`, by path
and function) and for its response models (`create_model_field`, by type), and hands every app the
same objects, which FastAPI only reads. The one thing it changes in a dependant after building it
is the list of its dependencies (route-level `dependencies=[...]`), so each caller gets a copy of
that list.

Test-only: the application code is untouched. If a FastAPI upgrade changes `get_dependant`'s
parameters, nothing is patched and the suite is just slower.
"""

import copy
import inspect
from collections.abc import Callable
from typing import Any

from fastapi import routing
from fastapi._compat import v2
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
_fields: dict[tuple[object, ...], v2.ModelField] = {}


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
