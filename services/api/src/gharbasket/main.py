"""FastAPI application factory.

Run with `just dev`, or `uvicorn gharbasket.main:create_app --factory`.
"""

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gharbasket import __version__
from gharbasket.platform import health
from gharbasket.platform.errors import register_error_handlers
from gharbasket.platform.logging import configure_logging
from gharbasket.platform.request_context import RequestContextMiddleware
from gharbasket.platform.settings import Settings, get_settings

API_PREFIX = "/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

    docs = settings.docs_enabled
    app = FastAPI(
        title="GharBasket API",
        version=__version__,
        description=(
            "Kitchen Autopilot and Society Captain platform. Errors use application/problem+json."
        ),
        openapi_url=f"{API_PREFIX}/openapi.json" if docs else None,
        docs_url=f"{API_PREFIX}/docs" if docs else None,
        redoc_url=None,
        # A trailing-slash redirect would point at the API's own host and drop the cookies that
        # browsers send through the same-origin proxies.
        redirect_slashes=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings

    v1 = APIRouter(prefix=API_PREFIX)
    v1.include_router(health.router)
    app.include_router(v1)

    register_error_handlers(app)

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID"],
        )
    # Added last, so it wraps everything else (including CORS preflight responses).
    app.add_middleware(RequestContextMiddleware)
    return app
