"""Write the API contract to a JSON file.

    python -m gharbasket.tools.export_openapi [path]   (default: services/api/openapi.json)

The committed `openapi.json` is what the frontend client (`packages/api-client`) is generated from,
and CI fails if it is out of date.
"""

import json
import sys
from pathlib import Path

from gharbasket.main import create_app
from gharbasket.platform.settings import Settings

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "openapi.json"


def build_spec() -> dict:
    return create_app(Settings(app_env="dev", _env_file=None)).openapi()  # pyright: ignore[reportCallIssue]


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    target = Path(args[0]) if args else DEFAULT_PATH
    target.write_text(json.dumps(build_spec(), indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
