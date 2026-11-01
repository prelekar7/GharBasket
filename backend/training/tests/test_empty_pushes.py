"""The API's internal endpoints take one item or more; a job with nothing to store must not send an
empty list (it would get 422 and fail the run, as export_snapshots did on staging, 1 Nov 2026)."""

import httpx2
import pytest
from gharbasket_training import features
from gharbasket_training.eval import report
from gharbasket_training.jobs import collect_mandi
from gharbasket_training.settings import Settings
from pydantic import SecretStr

pytestmark = pytest.mark.anyio


def refuse(request: httpx2.Request) -> httpx2.Response:
    raise AssertionError(f"nothing should be sent, got {request.method} {request.url.path}")


SETTINGS = Settings(api_url="https://api.test", jobs_token=SecretStr("op"))


async def test_no_features_sends_nothing() -> None:
    from datetime import UTC, datetime

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(refuse))
    assert await features.push(http, SETTINGS, datetime(2026, 11, 1, tzinfo=UTC), {}) == 0


async def test_no_index_rows_sends_nothing() -> None:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(refuse))
    assert await collect_mandi.push_index(http, SETTINGS, []) == 0


async def test_no_metric_rows_sends_nothing() -> None:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(refuse))
    assert await report.push(http, SETTINGS, []) == 0
