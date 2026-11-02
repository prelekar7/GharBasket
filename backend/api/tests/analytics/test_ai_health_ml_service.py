"""`GET /v1/analytics/ai-health` says whether the ML service is up and which models it serves
(M12 §3.8): read through `intel_gateway.api.ml_service_status`, never failing the page."""

from collections.abc import Iterator
from typing import Any

import httpx2
import pytest
import time_machine

from gharbasket.modules.intel_gateway import ml_status

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..intel_gateway.test_ml_status import SYNCED
from ..intel_gateway.test_plan_week import TOKEN
from ..subscriptions.test_cycles import NOW, as_user

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

NEHA = "+919800001011"  # admin


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    ml_status.reset_cache()
    yield
    ml_status.reset_cache()


def ml_client(handler: Any) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


async def health(clients: ClientFactory, google: FakeGoogle, **settings: Any) -> dict[str, Any]:
    api = clients(**settings)
    neha, _ = await as_user(api, google, NEHA)
    answer = await api.get("/v1/analytics/ai-health", headers=neha)
    assert answer.status_code == 200, answer.text
    return answer.json()["ml_service"]


@time_machine.travel(NOW, tick=False)
async def test_up_shows_the_served_models_and_the_sync(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    seen: list[httpx2.Request] = []

    def up(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=SYNCED)

    ml = await health(
        clients,
        google,
        http=ml_client(up),
        ml_service_url="http://ml.test",
        ml_service_token=TOKEN,
    )

    assert ml["status"] == "up"
    assert ml["served"] == [
        {"model": "cp-sat-planner", "version": "v1"},
        {"model": "forecast", "version": "20261101"},
    ]
    assert ml["registry_sync"] == {
        "last_success_at": "2026-11-02T03:00:00Z",
        "last_failure_at": "2026-11-01T03:00:00Z",
        "last_error": "R2 timed out",
    }
    assert ml["checked_at"]
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"


@time_machine.travel(NOW, tick=False)
async def test_down_when_the_service_does_not_answer(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    def asleep(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("asleep", request=request)

    ml = await health(
        clients,
        google,
        http=ml_client(asleep),
        ml_service_url="http://ml.test",
        ml_service_token=TOKEN,
    )

    assert (ml["status"], ml["served"], ml["registry_sync"]) == ("down", [], None)
    assert ml["checked_at"]


@time_machine.travel(NOW, tick=False)
async def test_not_configured_without_a_url(clients: ClientFactory, google: FakeGoogle) -> None:
    def never(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("no ML service is set, so nothing is asked")

    ml = await health(clients, google, http=ml_client(never))

    assert (ml["status"], ml["served"], ml["registry_sync"]) == ("not_configured", [], None)
