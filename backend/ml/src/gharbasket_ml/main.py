"""ML service application. Run with `just dev-ml`, or
`uvicorn gharbasket_ml.main:create_app --factory --port 8100`.

Only the API calls it (through `intel_gateway.api`), with the shared service token.
"""

import hmac
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from gharbasket_ml import __version__, assignment, pantry, ranker, registry, routing
from gharbasket_ml.planner import plan_week
from gharbasket_ml.schemas import (
    AssignRequest,
    AssignResponse,
    PantryRequest,
    PantryResponse,
    PlanRequest,
    PlanResponse,
    RankRequest,
    RankResponse,
    RouteRequest,
    RouteResponse,
)
from gharbasket_ml.settings import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


class Unauthorized(Exception):
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
        "/basket/rank",
        dependencies=[Depends(require_service_token)],
        summary="Rank next-basket candidates (LightGBM LambdaRank from the registry, M10 §3.7)",
    )
    async def basket_rank(
        body: RankRequest, settings: Annotated[Settings, Depends(get_settings)]
    ) -> RankResponse:
        return ranker.rank(body, settings.models_dir)

    @app.post(
        "/assign/kitchens",
        dependencies=[Depends(require_service_token)],
        summary="Tomorrow's tiffin portions to home kitchens, fairly (CP-SAT, M10 §3.5)",
    )
    async def assign_kitchens(body: AssignRequest) -> AssignResponse:
        return await run_in_threadpool(assignment.assign, body)

    @app.post(
        "/route/solve",
        dependencies=[Depends(require_service_token)],
        summary="One delivery slot's van routes in two waves (CVRPTW with pickups, M10 §3.4)",
    )
    async def route_solve(
        body: RouteRequest, settings: Annotated[Settings, Depends(get_settings)]
    ) -> RouteResponse:
        nodes = routing.nodes_of(body)
        points = [(n.lat, n.lng) for n in nodes]
        matrix = await routing.osrm_matrix(settings.osrm_url, points) if settings.osrm_url else None
        source = "osrm" if matrix is not None else "haversine"
        if matrix is None:
            matrix = routing.haversine_matrix(points)
        inst = routing.Instance(nodes, matrix[0], matrix[1], body)
        return await run_in_threadpool(routing.plan, inst, body.time_limit_s, source)

    @app.post(
        "/plan/week",
        dependencies=[Depends(require_service_token)],
        summary="A week of meals for one household (CP-SAT, M10 §3.1)",
    )
    async def plan(body: PlanRequest) -> PlanResponse:
        # The solver is CPU-bound: keep it off the event loop.
        return await run_in_threadpool(plan_week, body)

    return app
