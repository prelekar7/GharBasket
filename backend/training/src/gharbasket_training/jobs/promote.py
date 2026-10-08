"""`just promote <model> <version>` (ADR-0025): the only thing that moves a model's `latest.json`
in the registry (`models/` in `gb-mlflow`, or a local `--out` folder with the same layout without
the prefix, as `train_ranker --out` and `refit_pantry --out` write it). The same command rolls
back to an older version.

Champion/challenger: a version is promoted only when its card says it beats its baseline out of
sample (the basket ranker: NDCG@10 above both baselines; the pantry priors: MAE below the rule
table's on the held-out weeks); `--force` promotes anyway and records it in the pointer.

How serving follows the pointer:
- `basket_ranker`: the Space re-reads `basket_ranker/latest.json` every `MODELS_RELOAD_S`.
- `pantry_priors`: M3 reads `intel.pantry_priors`, so promotion first pushes the version's
  priors to `POST /v1/internal/intel/pantry-priors` (replacing the stored ones; needs `API_URL`
  and `JOBS_TOKEN`) and moves the pointer only once the API took them. `--no-push` moves the
  pointer alone (a local registry, tests); serving then keeps the previous priors.

    uv run --directory backend/training python -m gharbasket_training.jobs.promote \\
        basket_ranker 2026-10-05-synthetic [--out data/models] [--force] [--no-push]
"""

import argparse
import asyncio
import json
import math
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2

from gharbasket_training.lake import Registry, open_registry
from gharbasket_training.pantry_priors import beats_rule_table
from gharbasket_training.settings import Settings

# model → (artefact that must exist, the card holding its metrics)
ARTEFACTS = {
    "basket_ranker": ("model.txt", "card.json"),
    "pantry_priors": ("priors.json", "priors.json"),
}


def _ranker_beats(metrics: dict[str, dict[str, float]]) -> tuple[bool, str]:
    model = metrics.get("lambdarank", {}).get("ndcg_at_10", math.nan)
    baselines = {
        name: metrics.get(name, {}).get("ndcg_at_10", math.nan)
        for name in ("repeat_last_week", "top_frequency")
    }
    if model != model or any(v != v for v in baselines.values()):
        return False, "no leave-last-basket-out NDCG@10 for the model and both baselines"
    best = max(baselines.values())
    verdict = ">" if model > best else "<="
    return model > best, f"lambdarank NDCG@10 {model:.4f} {verdict} best baseline {best:.4f}"


def card(registry: Registry, model: str, version: str) -> dict[str, Any]:
    """The version's card; refuses a model or version that is not in the registry."""
    if model not in ARTEFACTS:
        raise SystemExit(f"unknown model {model!r}; one of {', '.join(sorted(ARTEFACTS))}")
    artefact, card_name = ARTEFACTS[model]
    if registry.lake.read_bytes(registry.key(model, version, artefact)) is None:
        raise SystemExit(f"{registry.key(model, version, artefact)} is not in the registry")
    body = registry.lake.read_bytes(registry.key(model, version, card_name))
    return json.loads(body) if body else {}


def challenge(model: str, document: dict[str, Any], *, force: bool) -> str:
    """The champion/challenger check: the reason, or SystemExit unless `force`."""
    metrics = document.get("metrics") or {}
    beats, why = (_ranker_beats if model == "basket_ranker" else beats_rule_table)(metrics)
    if not beats and not force:
        raise SystemExit(f"not promoted: {why}; pass --force to promote it anyway")
    return why if beats else f"forced: {why}"


def point(registry: Registry, model: str, version: str, why: str) -> dict[str, Any]:
    previous = registry.lake.read_bytes(registry.key(model, "latest.json"))
    pointer: dict[str, Any] = {
        "version": version,
        "promoted_at": datetime.now(UTC).isoformat(),
        "why": why,
    }
    if previous is not None:
        pointer["previous"] = json.loads(previous).get("version")
    registry.lake.write_bytes(
        registry.key(model, "latest.json"), (json.dumps(pointer) + "\n").encode()
    )
    return pointer


def promote(registry: Registry, model: str, version: str, *, force: bool = False) -> dict[str, Any]:
    """Check the version against its baseline and move the pointer (no push)."""
    why = challenge(model, card(registry, model, version), force=force)
    return point(registry, model, version, why)


async def push_priors(
    http: httpx2.AsyncClient, settings: Settings, document: dict[str, Any]
) -> int:
    """The promoted version's priors to the API, replacing the stored ones."""
    if not settings.api_url or settings.jobs_token is None:
        raise SystemExit(
            "set API_URL and JOBS_TOKEN to push the promoted priors (or pass --no-push)"
        )
    answer = await http.post(
        f"{settings.api_url.rstrip('/')}/v1/internal/intel/pantry-priors",
        json={
            "version": document["version"],
            "items": [
                {
                    "cohort": item["cohort"],
                    "product": item["product"],
                    "prior_mean_per_ae": item["prior_mean_per_ae"],
                    "prior_weeks": item["prior_weeks"],
                }
                for item in document["items"]
            ],
        },
        headers={"Authorization": f"Bearer {settings.jobs_token.get_secret_value()}"},
        timeout=settings.http_timeout_s,
    )
    answer.raise_for_status()
    return int(answer.json()["accepted"])


async def run(settings: Settings, args: argparse.Namespace) -> dict[str, Any]:
    registry = open_registry(settings, args.out)
    document = card(registry, args.model, args.version)
    why = challenge(args.model, document, force=args.force)
    pushed = None
    if args.model == "pantry_priors" and not args.no_push:
        if not document.get("items"):
            raise SystemExit(f"{args.version} has no learned priors to serve")
        async with httpx2.AsyncClient() as http:
            pushed = await push_priors(http, settings, document)
    pointer = point(registry, args.model, args.version, why)
    return pointer | ({"pushed": pushed} if pushed is not None else {})


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Promote (or roll back) a model version.")
    parser.add_argument("model", choices=sorted(ARTEFACTS))
    parser.add_argument("version", help="as in the registry, e.g. 2026-10-05-synthetic")
    parser.add_argument("--out", type=Path, default=None, help="a local registry folder")
    parser.add_argument(
        "--force", action="store_true", help="promote even if it does not beat its baseline"
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="pantry_priors: move the pointer only (serving keeps the previous priors)",
    )
    args = parser.parse_args(argv)
    print(json.dumps(asyncio.run(run(Settings(), args))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
