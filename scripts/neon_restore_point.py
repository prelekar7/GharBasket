"""Make a Neon restore point of the database DATABASE_URL points at: a copy-on-write branch of
its branch, with no compute (it costs storage only for what changes afterwards); and roll back.

    infisical run --env=staging -- uv run python scripts/neon_restore_point.py create before-demo
    GB_ALLOW_REMOTE_SEED=<host> infisical run --env=staging -- \
        uv run python scripts/neon_restore_point.py rollback before-demo-20261102T0530Z

Used by scripts/staging-demo.sh before it loads demo data (handbook 12). Needs NEON_API_KEY and,
for a personal key, NEON_PROJECT_ID: without it the projects the key lists are searched for the
URL's endpoint, and `GET /projects` without `org_id` does not list an organisation's projects. The
branch is named `<prefix>-<UTC timestamp>`, e.g. `before-demo-20261102T0530Z`. It never prints the
key or the database URL.

Neon's free plan allows 10 branches, and a branch with children cannot be deleted. Staging is not
a root branch, so it has no point-in-time restore or snapshots; a rollback restores it from the
restore point, which keeps the replaced state as a new branch and makes the restore point staging's
parent: both stay for good. So `create` first deletes earlier restore points nothing hangs off
(loads that were kept), and refuses when the new point and a rollback would not fit.
"""

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import sleep as _sleep
from typing import Any

try:  # python.org's macOS Python has no root certificates of its own
    import certifi

    TLS = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    TLS = ssl.create_default_context()

API = "https://console.neon.tech/api/v2"
TIMEOUT_S = 30
BRANCH_LIMIT = 10  # Neon Free, per project
READY_WAIT_S = 180

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


def branches_of(call: Call, project: str) -> list[dict[str, Any]]:
    return _ok(*call("GET", f"/projects/{project}/branches?limit=100", None), "listing branches")[
        "branches"
    ]


def computes_of(call: Call, project: str) -> set[str]:
    """Ids of the branches that have a compute."""
    listed = _ok(*call("GET", f"/projects/{project}/endpoints", None), "listing computes")
    return {endpoint["branch_id"] for endpoint in listed.get("endpoints", [])}


def _is_point(name: str, prefix: str) -> bool:
    stamp = name.removeprefix(f"{prefix}-")
    try:
        datetime.strptime(stamp, "%Y%m%dT%H%MZ")  # only the shape
    except ValueError:
        return False
    return name != stamp


def kept_points(
    branches: Sequence[dict[str, Any]], computes: set[str], parent: str, prefix: str
) -> list[dict[str, Any]]:
    """Earlier restore points of `parent` that nothing hangs off: their load was kept."""
    parents = {branch.get("parent_id") for branch in branches}
    return [
        branch
        for branch in branches
        if branch.get("parent_id") == parent
        and _is_point(branch["name"], prefix)
        and branch["id"] not in parents
        and branch["id"] not in computes
        and not branch.get("default")
        and not branch.get("protected")
    ]


def create_restore_point(
    call: Call,
    database_url: str,
    prefix: str,
    now: datetime,
    project_id: str | None = None,
    *,
    say: Callable[[str], None] = print,
) -> RestorePoint:
    endpoint = endpoint_of(database_url)
    project, parent = find_endpoint(call, endpoint, project_id)
    parent_branch = _ok(*call("GET", f"/projects/{project}/branches/{parent}", None), "branch")
    branches = branches_of(call, project)
    for old in kept_points(branches, computes_of(call, project), parent, prefix):
        _ok(
            *call("DELETE", f"/projects/{project}/branches/{old['id']}", None),
            f"deleting {old['name']}",
        )
        branches = [branch for branch in branches if branch["id"] != old["id"]]
        say(f"deleted the earlier restore point {old['name']} ({old['id']}): its load was kept")
    if len(branches) + 2 > BRANCH_LIMIT:
        raise NeonError(
            f"the project has {len(branches)} of Neon's {BRANCH_LIMIT} branches; this restore "
            "point and a rollback from it need 2 more. Delete a branch nobody uses that has no "
            "children (an archived dev branch) and run again"
        )
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


def rollback(
    call: Call,
    database_url: str,
    point_name: str,
    now: datetime,
    project_id: str | None = None,
    *,
    sleep: Callable[[float], None] = _sleep,
) -> tuple[str, str]:
    """Restore the database's branch from the head of its restore point `point_name` (a name or
    an id). Returns (branch name, name of the branch that keeps the replaced state)."""
    project, parent = find_endpoint(call, endpoint_of(database_url), project_id)
    branches = branches_of(call, project)
    by_id = {branch["id"]: branch for branch in branches}
    found = [b for b in branches if point_name in (b["name"], b["id"])]
    if len(found) != 1 or found[0].get("parent_id") != parent:
        raise NeonError(
            f"{point_name} is not a restore point of {by_id.get(parent, {}).get('name', parent)}"
            " (a branch made from it, as `create` does)"
        )
    point, target = found[0], by_id[parent]
    kept = f"{target['name']}-with-demo-{now.astimezone(UTC):%Y%m%dT%H%MZ}"
    if len(branches) + 1 > BRANCH_LIMIT:
        raise NeonError(
            f"the project has {len(branches)} of Neon's {BRANCH_LIMIT} branches; a rollback keeps "
            "the replaced state as one more. Delete a branch nobody uses first"
        )
    restored = _ok(
        *call(
            "POST",
            f"/projects/{project}/branches/{parent}/restore",
            {"source_branch_id": point["id"], "preserve_under_name": kept},
        ),
        f"restoring {target['name']} from {point['name']}",
    )
    wait_for(call, project, [op["id"] for op in restored.get("operations", [])], sleep)
    return target["name"], kept


def wait_for(
    call: Call, project: str, operations: list[str], sleep: Callable[[float], None]
) -> None:
    """Until Neon has finished each operation; a failed one raises."""
    waited, pending = 0, list(operations)
    while pending:
        for op in list(pending):
            found = _ok(*call("GET", f"/projects/{project}/operations/{op}", None), "operation")
            status = found["operation"]["status"]
            if status in ("finished", "skipped"):
                pending.remove(op)
            elif status in ("failed", "error", "cancelled", "cancelling"):
                action = found["operation"].get("action", "operation")
                raise NeonError(f"Neon's {action} {op} ended {status}; check the console")
        if pending:
            if waited >= READY_WAIT_S:
                raise NeonError(
                    f"Neon is still restoring after {READY_WAIT_S} s; check the console"
                )
            sleep(3)
            waited += 3


def restore_help(point: RestorePoint) -> list[str]:
    at = point.at.strftime("%Y-%m-%dT%H:%M:%SZ")
    return [
        f"restore point: branch {point.branch_name} ({point.branch_id}) of "
        f"{point.parent_name} ({point.parent_id}), project {point.project_id}, at {at}",
        f"to undo the demo data, restore {point.parent_name} from it (the URL stays the same):",
        f"  GB_ALLOW_REMOTE_SEED=<host> infisical run --env=staging -- just staging-rollback "
        f"{point.branch_name}",
        f"  it keeps the replaced state as {point.parent_name}-with-demo-<UTC time>; that branch "
        "and the restore point then stay for good (Neon cannot delete a branch with children)",
        "keep the data, and the next load deletes this restore point itself",
    ]


def main(argv: list[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(description="Make, or roll back to, a Neon restore point.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="a restore point of DATABASE_URL's branch")
    create.add_argument("prefix", help="branch name prefix, e.g. before-demo")
    back = commands.add_parser("rollback", help="restore DATABASE_URL's branch from a point")
    back.add_argument("point", help="the restore point's name or id")
    args = parser.parse_args(argv)
    key = environ.get("NEON_API_KEY")
    url = environ.get("DATABASE_URL_DIRECT") or environ.get("DATABASE_URL")
    if not key or not url:
        parser.error(
            "needs NEON_API_KEY and DATABASE_URL (run under `infisical run --env=staging`)"
        )
    project_id = environ.get("NEON_PROJECT_ID")
    if args.command == "rollback":
        host = urllib.parse.urlsplit(url).hostname or ""
        if environ.get("GB_ALLOW_REMOTE_SEED") != host:
            print(
                f"rolling back replaces the data at {host}: run again with "
                f"GB_ALLOW_REMOTE_SEED={host}",
                file=sys.stderr,
            )
            return 2
        try:
            branch, kept = rollback(
                http_call(key), url, args.point, datetime.now(UTC), project_id, sleep=_sleep
            )
        except NeonError as exc:
            print(f"no rollback: {exc}", file=sys.stderr)
            return 1
        print(f"{branch} is back at {args.point}; the replaced state is the branch {kept}")
        return 0
    try:
        point = create_restore_point(
            http_call(key), url, args.prefix, datetime.now(UTC), project_id
        )
    except NeonError as exc:
        print(f"no restore point: {exc}", file=sys.stderr)
        return 1
    for line in restore_help(point):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
