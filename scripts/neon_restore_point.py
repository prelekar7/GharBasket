"""Make a Neon restore point of the database DATABASE_URL points at: a copy-on-write branch of
its branch, with no compute (it costs storage only for what changes afterwards).

    infisical run --env=staging -- uv run python scripts/neon_restore_point.py before-demo

Used by scripts/staging-demo.sh before it loads demo data (handbook 12). Needs NEON_API_KEY and,
for a personal key, NEON_PROJECT_ID: without it the projects the key lists are searched for the
URL's endpoint, and `GET /projects` without `org_id` does not list an organisation's projects. The
branch is named `<prefix>-<UTC timestamp>`, e.g. `before-demo-20261102T0530Z`, and the script prints
how to restore from it. It never prints the key or the database URL.
"""

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

try:  # python.org's macOS Python has no root certificates of its own
    import certifi

    TLS = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    TLS = ssl.create_default_context()

API = "https://console.neon.tech/api/v2"
TIMEOUT_S = 30

# (method, path, body) -> (status, json body); replaced in tests.
Call = Callable[[str, str, dict[str, Any] | None], tuple[int, dict[str, Any]]]


class NeonError(Exception):
    pass


@dataclass(frozen=True)
class RestorePoint:
    project_id: str
    parent_id: str  # the branch the database lives on
    parent_name: str
    branch_id: str
    branch_name: str
    at: datetime  # just before the branch was made


def endpoint_of(database_url: str) -> str:
    """`ep-…` from the URL's host; the pooled host (`ep-…-pooler`) names the same endpoint."""
    host = urllib.parse.urlsplit(database_url).hostname or ""
    endpoint = host.split(".", 1)[0].removesuffix("-pooler")
    if not endpoint.startswith("ep-") or not host.endswith(".neon.tech"):
        raise NeonError(f"{host or 'the database URL'} is not a Neon host")
    return endpoint


def branch_name(prefix: str, now: datetime) -> str:
    return f"{prefix}-{now.astimezone(UTC):%Y%m%dT%H%MZ}"


def http_call(key: str) -> Call:
    def call(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        req = urllib.request.Request(  # noqa: S310 (a fixed https URL)
            API + path,
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=TLS) as resp:  # noqa: S310
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as err:
            raw = err.read()
            try:
                return err.code, json.loads(raw) if raw else {}
            except ValueError:
                return err.code, {"message": raw.decode(errors="replace")[:300]}

    return call


def _ok(status: int, body: dict[str, Any], what: str) -> dict[str, Any]:
    if not 200 <= status < 300:
        raise NeonError(f"{what}: Neon answered {status} {body.get('message', '')}".rstrip())
    return body


def find_endpoint(call: Call, endpoint: str, project_id: str | None) -> tuple[str, str]:
    """(project id, branch id) of the endpoint, in NEON_PROJECT_ID or any project the key sees."""
    if project_id:
        candidates = [project_id]
    else:
        listed = _ok(*call("GET", "/projects?limit=100", None), "listing projects")
        candidates = [p["id"] for p in listed.get("projects", [])]
    for candidate in candidates:
        status, body = call("GET", f"/projects/{candidate}/endpoints/{endpoint}", None)
        if status == 404 and not project_id:
            continue
        found = _ok(status, body, f"endpoint {endpoint} in project {candidate}")["endpoint"]
        return candidate, found["branch_id"]
    raise NeonError(
        f"no project this NEON_API_KEY lists has the endpoint {endpoint}: set NEON_PROJECT_ID "
        "(a personal key does not list an organisation's projects)"
    )


def create_restore_point(
    call: Call, database_url: str, prefix: str, now: datetime, project_id: str | None = None
) -> RestorePoint:
    endpoint = endpoint_of(database_url)
    project, parent = find_endpoint(call, endpoint, project_id)
    parent_branch = _ok(*call("GET", f"/projects/{project}/branches/{parent}", None), "branch")
    name = branch_name(prefix, now)
    created = _ok(
        *call(
            "POST",
            f"/projects/{project}/branches",
            {"branch": {"name": name, "parent_id": parent}},  # no endpoints: no compute
        ),
        f"creating branch {name}",
    )["branch"]
    return RestorePoint(
        project, parent, parent_branch["branch"]["name"], created["id"], name, now.astimezone(UTC)
    )


def restore_help(point: RestorePoint) -> list[str]:
    at = point.at.strftime("%Y-%m-%dT%H:%M:%SZ")
    return [
        f"restore point: branch {point.branch_name} ({point.branch_id}) of "
        f"{point.parent_name} ({point.parent_id}), project {point.project_id}, at {at}",
        f"to undo the demo data, restore {point.parent_name} (the URL stays the same):",
        f"  within Neon's history window (6 hours on the Free plan): console > Branches > "
        f"{point.parent_name} > Restore > from history, {at}",
        f"  any time, from the branch's head: neonctl branches restore {point.parent_id} "
        f"{point.branch_id} --preserve-under-name {point.parent_name}-with-demo "
        f"--project-id {point.project_id}",
        f"    (API: POST {API}/projects/{point.project_id}/branches/{point.parent_id}/restore "
        f'with {{"source_branch_id": "{point.branch_id}", '
        f'"preserve_under_name": "{point.parent_name}-with-demo"}})',
        f"  or add a compute to {point.branch_name} and point DATABASE_URL at it",
        "delete the branch in the console once it is no longer needed (Neon free: 10 branches)",
    ]


def main(argv: list[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(description="Make a Neon restore point of DATABASE_URL.")
    parser.add_argument("prefix", help="branch name prefix, e.g. before-demo")
    args = parser.parse_args(argv)
    key = environ.get("NEON_API_KEY")
    url = environ.get("DATABASE_URL_DIRECT") or environ.get("DATABASE_URL")
    if not key or not url:
        parser.error(
            "needs NEON_API_KEY and DATABASE_URL (run under `infisical run --env=staging`)"
        )
    try:
        point = create_restore_point(
            http_call(key), url, args.prefix, datetime.now(UTC), environ.get("NEON_PROJECT_ID")
        )
    except NeonError as exc:
        print(f"no restore point: {exc}", file=sys.stderr)
        return 1
    for line in restore_help(point):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
