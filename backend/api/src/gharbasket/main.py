"""FastAPI application factory.

Run with `just dev`, or `uvicorn gharbasket.main:create_app --factory`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gharbasket import __version__
from gharbasket.modules.analytics.router import router as analytics_router
from gharbasket.modules.assistant.router import router as assistant_router
from gharbasket.modules.billing.razorpay import RazorpayClient
from gharbasket.modules.billing.router import router as billing_router
from gharbasket.modules.catalog.router import router as catalog_router
from gharbasket.modules.community.router import router as community_router
from gharbasket.modules.hub_ops.router import router as hub_ops_router
from gharbasket.modules.identity.firebase import FirebaseTokenVerifier
from gharbasket.modules.identity.router import router as identity_router
from gharbasket.modules.intel_gateway.router import router as intel_gateway_router
from gharbasket.modules.logistics.router import router as logistics_router
from gharbasket.modules.notifications.router import router as notifications_router
from gharbasket.modules.orders.router import router as orders_router
from gharbasket.modules.partners.router import router as partners_router
from gharbasket.modules.registry import load_modules
from gharbasket.modules.subscriptions.router import router as subscriptions_router
from gharbasket.platform import files, health, jobs, openapi, outbox
from gharbasket.platform.db import create_engine, create_sessionmaker
from gharbasket.platform.errors import register_error_handlers
from gharbasket.platform.idempotency import REPLAY_HEADER, register_idempotency
from gharbasket.platform.logging import configure_logging
from gharbasket.platform.request_context import RequestContextMiddleware
from gharbasket.platform.settings import Settings, get_settings
from gharbasket.platform.valkey import create_valkey

API_PREFIX = "/v1"

# One router per business module, in M-number order (gharbasket.modules.MODULES).
MODULE_ROUTERS = (
    identity_router,
    catalog_router,
    subscriptions_router,
    orders_router,
    billing_router,
    hub_ops_router,
    logistics_router,
    partners_router,
    community_router,
    intel_gateway_router,
    notifications_router,
    analytics_router,
    assistant_router,
)


def _lifespan(settings: Settings):
    """Opens the database engine and Valkey client at startup and closes them at shutdown.

    Neither connects until first use, so the app starts even when the database is asleep or down.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database_url, valkey_url = settings.database_url, settings.valkey_url
        engine = create_engine(database_url.get_secret_value()) if database_url else None
        valkey = create_valkey(valkey_url.get_secret_value()) if valkey_url else None
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine) if engine else None
        app.state.valkey = valkey
        app.state.storage = files.create_store(settings)
        # Outbound HTTP (Google's Firebase keys now; Razorpay later): short timeouts.
        http = httpx2.AsyncClient(timeout=httpx2.Timeout(5.0))
        app.state.http = http
        app.state.razorpay = (
            RazorpayClient(
                http,
                settings.razorpay_key_id,  # pyright: ignore[reportArgumentType]
                settings.razorpay_key_secret.get_secret_value(),  # pyright: ignore[reportOptionalMemberAccess]
            )
            if settings.razorpay_enabled
            else None
        )
        app.state.firebase_verifier = (
            FirebaseTokenVerifier(settings.firebase_project_id, http)
            if settings.firebase_project_id
            else None
        )
        try:
            yield
        finally:
            await outbox.drain()  # let an after-commit relay finish before the engine goes
            await http.aclose()
            if valkey is not None:
                await valkey.aclose()
            if engine is not None:
                await engine.dispose()

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    load_modules()  # every module's jobs and outbox consumers, before anything relays

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
        lifespan=_lifespan(settings),
    )
    app.dependency_overrides[get_settings] = lambda: settings

    v1 = APIRouter(prefix=API_PREFIX)
    v1.include_router(health.router)
    v1.include_router(jobs.router)
    v1.include_router(files.router)
    for module_router in MODULE_ROUTERS:
        v1.include_router(module_router)
    app.include_router(v1)

    register_error_handlers(app)
    register_idempotency(app)
    openapi.install(app)

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID", REPLAY_HEADER],
        )
    # Added last, so it wraps everything else (including CORS preflight responses).
    app.add_middleware(RequestContextMiddleware)
    return app
