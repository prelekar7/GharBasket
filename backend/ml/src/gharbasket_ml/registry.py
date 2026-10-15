"""What this service is serving (M10 §3 `/models`, ADR-0025).

Solvers are built in and versioned with the code. Learned models arrive later from the R2
registry (`s3://gb-mlflow/models/<name>/<version>/` with a `latest.json` pointer per model); until
then `MODELS_MANIFEST` may point at a local JSON file of the same shape, mostly for tests.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx2

from gharbasket_ml import __version__

log = logging.getLogger(__name__)

BUILTIN = (
    ("planner", "cpsat-v0", "solver"),
    ("routing", "cvrptw-waves-v0", "solver"),
    ("pantry", "pantry-gamma-poisson-v0", "model"),
    ("assignment", "assignment-cpsat-v0", "solver"),
)


@dataclass(frozen=True)
class Served:
    name: str
    version: str
    kind: str  # solver, learned
    source: str  # builtin, manifest


UNREADABLE = "unreadable"  # the version reported for a pointer or manifest we cannot parse


def _version(pointer: Path) -> str:
    try:
        version = json.loads(pointer.read_text()).get("version")
    except (OSError, ValueError, AttributeError):
        return UNREADABLE
    return version if isinstance(version, str) and version else UNREADABLE


def served_models(manifest: Path | None, models_dir: Path | None = None) -> list[Served]:
    """Built-in solvers, then learned models. A malformed manifest or `latest.json` is listed
    with version `unreadable` (and logged) instead of failing `/models` and `/health/ai`."""
    models = [Served(name, version, kind, "builtin") for name, version, kind in BUILTIN]
    if manifest is not None and manifest.exists():
        try:
            entries = json.loads(manifest.read_text()).get("models", [])
            listed = [Served(e["name"], e["version"], "learned", "manifest") for e in entries]
        except (OSError, ValueError, AttributeError, KeyError, TypeError):
            log.warning("unreadable models manifest %s", manifest)
            listed = [Served(manifest.name, UNREADABLE, "learned", "manifest")]
        models.extend(listed)
    if models_dir is not None and models_dir.exists():
        for pointer in sorted(models_dir.glob("*/latest.json")):
            version = _version(pointer)
            if version == UNREADABLE:
                log.warning("unreadable model pointer %s", pointer)
            models.append(Served(pointer.parent.name, version, "learned", "registry"))
    return models


async def osrm_status(
    osrm_url: str | None,
    timeout_s: float = 2.0,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> str:
    """`ok` when the road network answers a one-leg route, `down` when it does not,
    `not_configured` without a URL."""
    if not osrm_url:
        return "not_configured"
    probe = (
        f"{osrm_url.rstrip('/')}/route/v1/driving/73.0690,19.0300;73.1185,19.0005?overview=false"
    )
    try:
        async with httpx2.AsyncClient(timeout=timeout_s, transport=transport) as http:
            answer = await http.get(probe)
        return "ok" if answer.status_code == 200 and answer.json().get("code") == "Ok" else "down"
    except (httpx2.HTTPError, ValueError):
        return "down"


async def health(
    manifest: Path | None, osrm_url: str | None = None, models_dir: Path | None = None
) -> dict[str, object]:
    return {
        "status": "ok",
        "version": __version__,
        "models": [m.__dict__ for m in served_models(manifest, models_dir)],
        "osrm": await osrm_status(osrm_url),
    }
