"""The routes analysed once per process (tests/fast_app.py) still make every app a complete one."""

from gharbasket.main import create_app

from . import fast_app
from .conftest import make_settings


def test_apps_built_from_the_kept_routes_document_the_same_api() -> None:
    fast_app.install()
    first = create_app(make_settings())
    second = create_app(make_settings(app_env="dev"))
    third = create_app(make_settings())

    assert fast_app._found  # pyright: ignore[reportPrivateUsage]
    assert len(first.openapi()["paths"]) > 100
    assert first.openapi() == second.openapi() == third.openapi()
