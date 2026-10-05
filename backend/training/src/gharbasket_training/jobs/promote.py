"""`just promote <model> <version>` (ADR-0025): point a model's `latest.json` in the registry at
a version that already exists there; the same command rolls back to an older one. The Space and
the API read the pointer, so a promotion takes effect without a deploy.

    uv run --directory backend/training python -m gharbasket_training.jobs.promote \\
        basket_ranker 2026-10-05 [--out data/models]
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from gharbasket_training.lake import Lake, open_lake
from gharbasket_training.settings import Settings

ARTEFACTS = {"basket_ranker": "model.txt", "pantry_priors": "priors.json"}


def promote(lake: Lake, model: str, version: str) -> dict[str, str]:
    artefact = ARTEFACTS.get(model)
    if artefact is None:
        raise SystemExit(f"unknown model {model!r}; one of {', '.join(sorted(ARTEFACTS))}")
    if lake.read_bytes(f"models/{model}/{version}/{artefact}") is None:
        raise SystemExit(f"models/{model}/{version}/{artefact} is not in the registry")
    previous = lake.read_bytes(f"models/{model}/latest.json")
    pointer = {"version": version, "promoted_at": datetime.now(UTC).isoformat()}
    if previous is not None:
        pointer["previous"] = json.loads(previous).get("version")
    lake.write_bytes(f"models/{model}/latest.json", (json.dumps(pointer) + "\n").encode())
    return pointer


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Promote (or roll back) a model version.")
    parser.add_argument("model", choices=sorted(ARTEFACTS))
    parser.add_argument("version")
    parser.add_argument("--out", type=Path, default=None, help="a local registry folder")
    args = parser.parse_args(argv)
    pointer = promote(open_lake(Settings(), args.out), args.model, args.version)
    print(json.dumps(pointer))
    return 0


if __name__ == "__main__":
    sys.exit(main())
