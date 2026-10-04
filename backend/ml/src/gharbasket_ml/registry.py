"""What this service is serving (M10 §3 `/models`, ADR-0025).

Solvers are built in and versioned with the code. Learned models arrive later from the R2
registry (`s3://gb-mlflow/models/<name>/<version>/` with a `latest.json` pointer per model); until
then `MODELS_MANIFEST` may point at a local JSON file of the same shape, mostly for tests.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from gharbasket_ml import __version__

BUILTIN = (
    ("planner", "cpsat-v0", "solver"),
    ("routing", "cvrptw-waves-v0", "solver"),
)


@dataclass(frozen=True)
class Served:
    name: str
    version: str
    kind: str  # solver, learned
    source: str  # builtin, manifest


def served_models(manifest: Path | None) -> list[Served]:
    models = [Served(name, version, kind, "builtin") for name, version, kind in BUILTIN]
    if manifest is not None and manifest.exists():
        for entry in json.loads(manifest.read_text()).get("models", []):
            models.append(Served(entry["name"], entry["version"], "learned", "manifest"))
    return models


def health(manifest: Path | None) -> dict[str, object]:
    return {
        "status": "ok",
        "version": __version__,
        "models": [m.__dict__ for m in served_models(manifest)],
        "osrm": "not_configured",  # the routing PR reports the road network here
    }
