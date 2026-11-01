"""`just cloud-ml <url> [token]`: does a deployed ML service answer? `/health`, `/health/ai`,
`/models`, a week plan for a small household, a pantry quantity, a basket ranking, a slot
price, a route and a kitchen assignment (each from a minimal valid body); prints one line each
(`<path> <status> <summary>`, or `<path> ERR <reason>`) and exits 1 unless all nine are 200."""

import asyncio
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any

import httpx2

SAMPLE_PLAN = {
    "household": {
        "size": 2,
        "diet": "veg",
        "cuisines": ["maharashtrian"],
        "weekday_cook_minutes": 30,
        "weekend_cook_minutes": 60,
        "no_cook_days": [],
    },
    "recipes": [
        {
            "slug": f"dish-{i}",
            "cuisine": "maharashtrian",
            "slots": ["breakfast", "lunch", "dinner"],
            "cook_minutes": 20,
            "diet": "veg",
            "jain_ok": True,
            "no_onion_garlic": True,
            "fasting_ok": True,
            "diabetic_ok": True,
            "cost_per_serving_paise": 1000 + i,
        }
        for i in range(6)
    ],
    "time_limit_s": 1.0,
}
SAMPLE_PANTRY = {"items": [{"product": "milk", "step": 500, "prior_mean": 1400}]}
SAMPLE_RANK = {
    "candidates": [
        {"product": "milk", "weeks_since": 1, "frequency": 0.9, "popularity": 0.8},
        {"product": "ghee", "weeks_since": 6, "frequency": 0.2, "popularity": 0.3},
    ],
    "top_k": 2,
}
_HUB = {"lat": 19.03, "lng": 73.069}
_MORNING = {"window_start_s": 8 * 3600, "window_end_s": 10 * 3600}
SAMPLE_SLOT = {  # one candidate society, one slot, no stops yet: a new trip in that slot
    "hub": _HUB,
    "depart_s": 6 * 3600 + 1800,
    "return_by_s": 13 * 3600,
    "vans": {"count": 1},
    "candidates": [{"id": "x", "lat": 19.04, "lng": 73.069}],
    "slots": [{"id": "s1", **_MORNING}],
}
SAMPLE_ROUTE = {
    "hub": _HUB,
    "depart_s": 6 * 3600 + 1800,
    "return_by_s": 11 * 3600 + 1800,
    "stops": [
        {"id": "a", "lat": 19.035, "lng": 73.075, "crates": 2, "kg_tenths": 100, **_MORNING},
        {"id": "b", "lat": 19.035, "lng": 73.08, "crates": 2, "kg_tenths": 100, **_MORNING},
    ],
    "vans": {"count": 1},
    "time_limit_s": 1.0,
}
SAMPLE_ASSIGN = {
    "orders": [
        {"id": "o1", "lat": 19.03, "lng": 73.07, "portions": 3, "skills": ["veg"]},
        {"id": "o2", "lat": 19.04, "lng": 73.06, "portions": 4, "skills": ["veg"]},
    ],
    "kitchens": [
        {
            "id": "k1",
            "lat": 19.031,
            "lng": 73.071,
            "max_portions": 20,
            "skills": ["veg"],
            "rating": 4.5,
            "earnings_paise": 0,
        }
    ],
    "time_limit_s": 1.0,
}


Body = dict[str, Any] | list[Any] | None
Describe = Callable[[Body], str]


def _version(body: Body) -> str:
    return str(body.get("version")) if isinstance(body, dict) else "None"


def _osrm(body: Body) -> str:
    return f"osrm={body.get('osrm') if isinstance(body, dict) else None}"


def _model_names(body: Body) -> str:
    names = [m.get("name") for m in body if isinstance(m, dict)] if isinstance(body, list) else []
    return str(names)


def _plan(body: Body) -> str:
    got = body if isinstance(body, dict) else {}
    return f"{got.get('status')} in {got.get('seconds')} s"


def _top_ranked(body: Body) -> str:
    ranked = body.get("ranked") if isinstance(body, dict) else None
    first = ranked[0] if isinstance(ranked, list) and ranked else None
    return f"top={first.get('product') if isinstance(first, dict) else None}"


def _slot_prices(body: Body) -> str:
    got = body if isinstance(body, dict) else {}
    priced = got.get("candidates")
    return (
        f"candidates={len(priced) if isinstance(priced, list) else None} source={got.get('source')}"
    )


def _route(body: Body) -> str:
    got = body if isinstance(body, dict) else {}
    routes = got.get("routes")
    return f"{got.get('status')} vans={len(routes) if isinstance(routes, list) else None}"


def _assignment(body: Body) -> str:
    got = body if isinstance(body, dict) else {}
    assigned = got.get("assignments")
    return f"{got.get('status')} assigned={len(assigned) if isinstance(assigned, list) else None}"


def _pantry_mean(body: Body) -> str:
    items = body.get("items") if isinstance(body, dict) else None
    first = items[0] if isinstance(items, list) and items else None
    return f"mean={first.get('mean') if isinstance(first, dict) else None}"


async def _call(
    http: httpx2.AsyncClient,
    method: str,
    path: str,
    describe: Describe,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
) -> str:
    """One line per endpoint: `<path> <status> <summary>`, or `<path> ERR <reason>` when the call
    fails (timeout, connection) or a 200 answer is not JSON."""
    try:
        response = await http.request(method, path, json=payload, headers=headers)
        body = response.json() if response.status_code == 200 else None
    except httpx2.HTTPError as error:
        return f"{path} ERR {type(error).__name__} {error}".rstrip()
    except ValueError as error:
        return f"{path} ERR not JSON: {error}"
    return f"{path} {response.status_code} {describe(body)}"


async def check(
    base_url: str,
    token: str | None,
    timeout_s: float = 90.0,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> list[str]:
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx2.AsyncClient(
        base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport
    ) as http:
        return [
            await _call(http, "GET", "/health", _version, {}),
            await _call(http, "GET", "/health/ai", _osrm, auth),
            await _call(http, "GET", "/models", _model_names, auth),
            await _call(http, "POST", "/plan/week", _plan, auth, SAMPLE_PLAN),
            await _call(http, "POST", "/pantry/quantities", _pantry_mean, auth, SAMPLE_PANTRY),
            await _call(http, "POST", "/basket/rank", _top_ranked, auth, SAMPLE_RANK),
            await _call(http, "POST", "/slots/price", _slot_prices, auth, SAMPLE_SLOT),
            await _call(http, "POST", "/route/solve", _route, auth, SAMPLE_ROUTE),
            await _call(http, "POST", "/assign/kitchens", _assignment, auth, SAMPLE_ASSIGN),
        ]


def passed(line: str) -> bool:
    return line.split(" ", 2)[1:2] == ["200"]


def main(
    argv: Sequence[str] | None = None, transport: httpx2.AsyncBaseTransport | None = None
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            "usage: python -m gharbasket_ml.smoke <url>  (token from ML_SERVICE_TOKEN)",
            file=sys.stderr,
        )
        return 2
    # The token comes from the environment: on a command line a tool may echo it (`just` does).
    token = (args[1] if len(args) > 1 and args[1] else None) or os.environ.get("ML_SERVICE_TOKEN")
    lines = asyncio.run(check(args[0], token, transport=transport))
    print("\n".join(lines))
    return 0 if all(passed(line) for line in lines) else 1


if __name__ == "__main__":
    sys.exit(main())
