"""`just cloud-ml <url> [token]`: does a deployed ML service answer? `/health`, `/health/ai`,
`/models`, a week plan for a small household and a pantry quantity; prints one line each."""

import asyncio
import sys
from collections.abc import Sequence

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


async def check(
    base_url: str,
    token: str | None,
    timeout_s: float = 90.0,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> list[str]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    lines = []
    async with httpx2.AsyncClient(
        base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport
    ) as http:
        health = await http.get("/health")
        lines.append(f"/health {health.status_code} {health.json().get('version')}")
        ai = await http.get("/health/ai", headers=headers)
        body = ai.json() if ai.status_code == 200 else {}
        lines.append(f"/health/ai {ai.status_code} osrm={body.get('osrm')}")
        models = await http.get("/models", headers=headers)
        names = [m["name"] for m in models.json()] if models.status_code == 200 else []
        lines.append(f"/models {models.status_code} {names}")
        plan = await http.post("/plan/week", json=SAMPLE_PLAN, headers=headers)
        answer = plan.json() if plan.status_code == 200 else {}
        lines.append(
            f"/plan/week {plan.status_code} {answer.get('status')} in {answer.get('seconds')} s"
        )
        pantry = await http.post("/pantry/quantities", json=SAMPLE_PANTRY, headers=headers)
        got = pantry.json() if pantry.status_code == 200 else {}
        lines.append(
            f"/pantry/quantities {pantry.status_code} mean={got.get('items', [{}])[0].get('mean')}"
        )
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: python -m gharbasket_ml.smoke <url> [token]", file=sys.stderr)
        return 2
    lines = asyncio.run(check(args[0], args[1] if len(args) > 1 else None))
    print("\n".join(lines))
    return 0 if all(" 200 " in line for line in lines) else 1


if __name__ == "__main__":
    sys.exit(main())
