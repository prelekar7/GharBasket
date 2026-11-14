"""ML service application. Run with `just dev-ml`, or
`uvicorn gharbasket_ml.main:create_app --factory --port 8100`.

Only the API calls it (through `intel_gateway.api`), with the shared service token.
"""

import asyncio
import contextlib
import hmac
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from gharbasket_ml import (
    __version__,
    assignment,
    assistant,
    model_sync,
    pantry,
    ranker,
    registry,
    routing,
    slots,
    tracing,
)
from gharbasket_ml.planner import plan_week
from gharbasket_ml.schemas import (
    AssignRequest,
    AssignResponse,
    AssistantTurnRequest,
    AssistantTurnResponse,
    PantryRequest,
    PantryResponse,
    PlanRequest,
    PlanResponse,
    RankRequest,
    RankResponse,
    RouteRequest,
    RouteResponse,
    SlotPriceRequest,
    SlotPriceResponse,
)
from gharbasket_ml.settings import Settings, get_settings

log = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


class Unauthorized(Exception):
    pass


class AssistantUnavailable(Exception):
    pass


class AssistantTimeout(Exception):
    pass


async def require_service_token(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    expected = settings.ml_service_token
    if expected is None:  # local development only (settings refuse this elsewhere)
        return
    given = credentials.credentials if credentials else ""
    if not hmac.compare_digest(given.encode(), expected.get_secret_value().encode()):
        raise Unauthorized


def _reload_ranker(models_dir: Path, reload_s: float) -> Callable[[list[str]], None]:
    """After a sync moved the ranker: re-read it now, in the sync's thread, so the new version
    is served at once and no request pays the load. Forced, not cleared: a new version that
    cannot be served keeps the last good model (ranker.load), never the fallback."""

    def reload(changed: list[str]) -> None:
        if ranker.MODEL_NAME in changed:
            ranker.load(models_dir, reload_s=reload_s, force=True)

    return reload


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Starts following the R2 registry in the background (when configured): startup and
    `/health` never wait for R2, and an R2 outage only shows in `/health/ai`."""
    settings: Settings = app.dependency_overrides.get(get_settings, get_settings)()
    sync: model_sync.ModelSync | None = None
    task: asyncio.Task[None] | None = None
    if (warning := settings.registry_sync_warning) is not None:
        log.warning("model registry: %s", warning)
    if settings.registry_sync_enabled:
        # A test hook: tests put a fake registry here before the app starts; unset in service.
        reader = getattr(app.state, "registry_reader", None) or model_sync.R2Reader.from_settings(
            settings
        )
        sync = model_sync.ModelSync(
            reader,
            settings.models_cache_dir,
            bucket=settings.s3_bucket_mlflow,
            deadline_s=settings.models_sync_deadline_s,
            on_change=_reload_ranker(settings.models_cache_dir, settings.models_reload_s),
        )
        task = asyncio.create_task(sync.run_forever(settings.models_reload_s))
    app.state.model_sync = sync
    try:
        yield
    finally:
        if sync is not None:
            sync.stop()  # a running sync gives up at its next piece or request
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if sync is not None:
            # The worker thread outlives the cancelled task: close the client only once it is
            # done (bounded by one request's timeouts).
            if not await asyncio.to_thread(sync.wait_idle, settings.models_sync_timeout_s + 5):
                log.warning("model registry: a sync was still running at shutdown")
            sync.close()
        await asyncio.to_thread(tracing.shutdown)  # at most 5 s with the collector down


def create_app(settings: Settings | None = None) -> FastAPI:
    # Deployed, the service is on the public internet but is only for the API: no interactive
    # docs and no schema listing. `settings` is None under `uvicorn --factory`, so it is read here.
    resolved = settings or get_settings()
    deployed = resolved.is_deployed
    # Log lines with trace ids; spans exported only when OTEL_EXPORTER_OTLP_ENDPOINT is set.
    tracing.configure_logging()
    tracing.setup(resolved)
    app = FastAPI(
        title="GharBasket ML service",
        version=__version__,
        description="Internal: called only by the GharBasket API (intel_gateway).",
        lifespan=lifespan,
        telemetry=tracing.FASTAPI_TELEMETRY_OFF,  # our own spans only (tracing.py)
        docs_url=None if deployed else "/docs",
        redoc_url=None if deployed else "/redoc",
        openapi_url=None if deployed else "/openapi.json",
    )
    app.state.model_sync = None
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings

    @app.exception_handler(Unauthorized)
    async def _unauthorized(request: Request, _exc: Exception) -> JSONResponse:
        tracing.record_problem("ml.unauthorized", 401)
        return JSONResponse(
            {
                "type": "urn:gharbasket:problem:ml.unauthorized",
                "title": "Unauthorized",
                "status": 401,
                "code": "ml.unauthorized",
                "instance": request.url.path,
            },
            status_code=401,
            media_type="application/problem+json",
        )

    @app.exception_handler(AssistantUnavailable)
    async def assistant_unavailable(request: Request, _exc: AssistantUnavailable) -> JSONResponse:
        tracing.record_problem("ml.assistant_unavailable", 503)
        return JSONResponse(
            {
                "type": "about:blank",
                "title": "Assistant unavailable",
                "status": 503,
                "detail": "ANTHROPIC_API_KEY is not set on this service.",
                "code": "ml.assistant_unavailable",
                "instance": request.url.path,
            },
            status_code=503,
            media_type="application/problem+json",
        )

    @app.exception_handler(AssistantTimeout)
    async def assistant_timeout(request: Request, _exc: AssistantTimeout) -> JSONResponse:
        tracing.record_problem("ml.assistant_timeout", 504)
        return JSONResponse(
            {
                "type": "about:blank",
                "title": "Assistant timed out",
                "status": 504,
                "detail": "The turn took longer than ASSISTANT_TURN_TIMEOUT_S.",
                "code": "ml.assistant_timeout",
                "instance": request.url.path,
            },
            status_code=504,
            media_type="application/problem+json",
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "gharbasket-ml", "version": __version__}

    @app.get(
        "/models",
        dependencies=[Depends(require_service_token)],
        summary="The models this service is serving, with versions (M10 §3)",
    )
    async def models(settings: Annotated[Settings, Depends(get_settings)]) -> list[dict[str, str]]:
        return [
            m.__dict__
            for m in registry.served_models(settings.models_manifest, settings.served_models_dir)
        ]

    @app.get(
        "/health/ai",
        dependencies=[Depends(require_service_token)],
        summary="Health with the served models and the road network",
    )
    async def health_ai(
        request: Request, settings: Annotated[Settings, Depends(get_settings)]
    ) -> dict[str, object]:
        body = await registry.health(
            settings.models_manifest, settings.osrm_url, settings.served_models_dir
        )
        sync: model_sync.ModelSync | None = request.app.state.model_sync
        body["registry_sync"] = sync.status() if sync is not None else {"enabled": False}
        return body

    @app.post(
        "/pantry/quantities",
        dependencies=[Depends(require_service_token)],
        summary="Weekly use per item with an 80 % interval (the pantry twin, M10 §3.3)",
    )
    async def pantry_quantities(body: PantryRequest) -> PantryResponse:
        return pantry.quantities(body)

    @app.post(
        "/assistant/turn",
        dependencies=[Depends(require_service_token)],
        summary="One turn of Ghar Saathi with the four tools (M13 §3); intents, never changes",
    )
    async def assistant_turn(
        body: AssistantTurnRequest,
        request: Request,
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> AssistantTurnResponse:
        if settings.anthropic_api_key is None:
            raise AssistantUnavailable()
        factory = getattr(request.app.state, "assistant_client", None)
        key = settings.anthropic_api_key.get_secret_value()
        client = (
            factory(key)
            if factory is not None
            else assistant.anthropic_client(key, settings.assistant_request_timeout_s)
        )
        try:
            async with asyncio.timeout(settings.assistant_turn_timeout_s):
                return await assistant.turn(body, client, settings.assistant_model)
        except TimeoutError as exc:
            raise AssistantTimeout() from exc

    @app.post(
        "/basket/rank",
        dependencies=[Depends(require_service_token)],
        summary="Rank next-basket candidates (LightGBM LambdaRank from the registry, M10 §3.7)",
    )
    async def basket_rank(
        body: RankRequest, settings: Annotated[Settings, Depends(get_settings)]
    ) -> RankResponse:
        # Off the event loop: a (re)load reads the model file under the ranker's lock.
        return await run_in_threadpool(
            ranker.rank, body, settings.served_models_dir, settings.models_reload_s
        )

    @app.post(
        "/assign/kitchens",
        dependencies=[Depends(require_service_token)],
        summary="Tomorrow's tiffin portions to home kitchens, fairly (CP-SAT, M10 §3.5)",
    )
    async def assign_kitchens(body: AssignRequest) -> AssignResponse:
        return await run_in_threadpool(assignment.assign, body, deterministic=False)

    @app.post(
        "/route/solve",
        dependencies=[Depends(require_service_token)],
        summary="One delivery slot's van routes in two waves (CVRPTW with pickups, M10 §3.4)",
    )
    async def route_solve(
        body: RouteRequest, settings: Annotated[Settings, Depends(get_settings)]
    ) -> RouteResponse:
        return await routing.solve(body, settings.osrm_url, settings.osrm_timeout_s)

    @app.post(
        "/slots/price",
        dependencies=[Depends(require_service_token)],
        summary="A society's marginal route cost per delivery slot (cheapest insertion, M10 §3.8)",
    )
    async def slots_price(
        body: SlotPriceRequest, settings: Annotated[Settings, Depends(get_settings)]
    ) -> SlotPriceResponse:
        return await slots.price(body, settings.osrm_url, settings.slot_osrm_timeout_s)

    @app.post(
        "/plan/week",
        dependencies=[Depends(require_service_token)],
        summary="A week of meals for one household (CP-SAT, M10 §3.1)",
    )
    async def plan(body: PlanRequest) -> PlanResponse:
        # The solver is CPU-bound: keep it off the event loop.
        return await run_in_threadpool(plan_week, body)

    # Outermost: every request is a span; the API's trace is continued only with the token.
    app.add_middleware(tracing.TracingMiddleware, token=resolved.ml_service_token)
    return app
