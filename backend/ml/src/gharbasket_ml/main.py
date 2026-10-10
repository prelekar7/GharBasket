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

from gharbasket_ml import __version__, pantry, registry
from gharbasket_ml.planner import plan_week
from gharbasket_ml.schemas import PantryRequest, PantryResponse, PlanRequest, PlanResponse
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
        return [m.__dict__ for m in registry.served_models(settings.models_manifest)]

    @app.get(
        "/health/ai",
        dependencies=[Depends(require_service_token)],
        summary="Health with the served models and the road network",
    )
    async def health_ai(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
        return await registry.health(settings.models_manifest, settings.osrm_url)

    @app.post(
        "/pantry/quantities",
        dependencies=[Depends(require_service_token)],
        summary="Weekly use per item with an 80 % interval (the pantry twin, M10 §3.3)",
    )
    async def pantry_quantities(body: PantryRequest) -> PantryResponse:
        return pantry.quantities(body)

    @app.post(
        "/plan/week",
        dependencies=[Depends(require_service_token)],
        summary="A week of meals for one household (CP-SAT, M10 §3.1)",
    )
    async def plan(body: PlanRequest) -> PlanResponse:
        # The solver is CPU-bound: keep it off the event loop.
        return await run_in_threadpool(plan_week, body)

    return app
