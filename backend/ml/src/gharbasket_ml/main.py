"""ML service application. Run with `just dev-ml`, or
`uvicorn gharbasket_ml.main:create_app --factory --port 8100`.

Only the API calls it (through `intel_gateway.api`), with the shared service token.
"""

import asyncio
import hmac
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from gharbasket_ml import (
    __version__,
    assignment,
    assistant,
    pantry,
    ranker,
    registry,
    routing,
    slots,
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


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        title="GharBasket ML service",
        version=__version__,
        description="Internal: called only by the GharBasket API (intel_gateway).",
    )
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings

    @app.exception_handler(Unauthorized)
    async def _unauthorized(request: Request, _exc: Exception) -> JSONResponse:
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
            for m in registry.served_models(settings.models_manifest, settings.models_dir)
        ]

    @app.get(
        "/health/ai",
        dependencies=[Depends(require_service_token)],
        summary="Health with the served models and the road network",
    )
    async def health_ai(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
        return await registry.health(
            settings.models_manifest, settings.osrm_url, settings.models_dir
        )

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
        return ranker.rank(body, settings.models_dir, settings.models_reload_s)

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

    return app
