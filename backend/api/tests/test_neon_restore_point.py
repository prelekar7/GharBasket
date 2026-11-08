"""scripts/neon_restore_point.py makes the Neon branch that scripts/staging-demo.sh restores from;
these tests drive it against a fake Neon API, without the network or a key."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "neon_restore_point", ROOT / "scripts" / "neon_restore_point.py"
)
assert _spec and _spec.loader
neon: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(neon)

POOLED = "postgresql://app:secret-pw@ep-cool-name-a1b2-pooler.ap-southeast-1.aws.neon.tech/neondb"
DIRECT = "postgresql://app:secret-pw@ep-cool-name-a1b2.ap-southeast-1.aws.neon.tech/neondb"
NOW = datetime(2026, 11, 2, 5, 30, 12, tzinfo=UTC)


def branch(id_: str, name: str, parent: str | None, **extra: Any) -> dict[str, Any]:
    return {"id": id_, "name": name, "parent_id": parent, **extra}


class FakeNeon:
    """Two projects; the second owns the endpoint, on its branch `production`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.branch_answer: tuple[int, dict[str, Any]] = (
            201,
            {"branch": {"id": "br-new-1", "name": "before-demo-20261102T0530Z"}},
        )
        self.branches = [
            branch("br-root", "main", None),
            branch("br-main", "production", "br-root", default=True),
        ]
        self.computes = ["br-main"]
        self.operations: dict[str, list[str]] = {}  # id -> statuses still to report

    def __call__(
        self, method: str, path: str, body: dict[str, Any] | None
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append((method, path, body))
        if path == "/projects?limit=100":
            return 200, {"projects": [{"id": "other-1"}, {"id": "gb-2"}]}
        if path == "/projects/gb-2/endpoints/ep-cool-name-a1b2":
            return 200, {"endpoint": {"id": "ep-cool-name-a1b2", "branch_id": "br-main"}}
        if path.startswith("/projects/") and "/endpoints/" in path:
            return 404, {"message": "endpoint not found"}
        if path == "/projects/gb-2/endpoints":
            return 200, {"endpoints": [{"id": f"ep-{b}", "branch_id": b} for b in self.computes]}
        if path == "/projects/gb-2/branches?limit=100":
            return 200, {"branches": self.branches}
        if method == "GET" and path == "/projects/gb-2/branches/br-main":
            return 200, {"branch": {"id": "br-main", "name": "production"}}
        if method == "POST" and path == "/projects/gb-2/branches":
            return self.branch_answer
        if method == "DELETE" and path.startswith("/projects/gb-2/branches/"):
            gone = path.rsplit("/", 1)[1]
            assert gone not in {b["parent_id"] for b in self.branches}, "Neon: has children"
            self.branches = [b for b in self.branches if b["id"] != gone]
            return 200, {"branch": {"id": gone}}
        if method == "POST" and path == "/projects/gb-2/branches/br-main/restore":
            self.operations = {"op-1": ["running", "finished"], "op-2": ["finished"]}
            return 200, {
                "branch": {"id": "br-main"},
                "operations": [{"id": "op-1"}, {"id": "op-2"}],
            }
        if path.startswith("/projects/gb-2/operations/"):
            statuses = self.operations[path.rsplit("/", 1)[1]]
            status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
            return 200, {"operation": {"status": status, "action": "reset_branch"}}
        raise AssertionError(f"unexpected call {method} {path}")


@pytest.mark.parametrize("url", [POOLED, DIRECT])
def test_the_endpoint_comes_from_the_pooled_or_direct_host(url: str) -> None:
    assert neon.endpoint_of(url) == "ep-cool-name-a1b2"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://gb:gb@localhost:5432/gharbasket",
        "postgresql://u:p@db.example.com/x",
        "postgresql://u:p@ep-x.example.com/x",
    ],
)
def test_a_url_that_is_not_neon_is_refused(url: str) -> None:
    with pytest.raises(neon.NeonError, match="not a Neon host"):
        neon.endpoint_of(url)


def test_the_branch_is_made_from_the_endpoints_branch_without_a_compute() -> None:
    fake = FakeNeon()
    point = neon.create_restore_point(fake, POOLED, "before-demo", NOW)

    assert point == neon.RestorePoint(
        "gb-2", "br-main", "production", "br-new-1", "before-demo-20261102T0530Z", NOW
    )
    method, path, body = fake.calls[-1]
    assert (method, path) == ("POST", "/projects/gb-2/branches")
    assert body == {"branch": {"name": "before-demo-20261102T0530Z", "parent_id": "br-main"}}
    help_text = "\n".join(neon.restore_help(point))
    assert "just staging-rollback before-demo-20261102T0530Z" in help_text
    assert "2026-11-02T05:30:12Z" in help_text
    assert "secret-pw" not in help_text


def test_a_given_project_id_skips_the_lookup() -> None:
    fake = FakeNeon()
    neon.create_restore_point(fake, DIRECT, "before-demo", NOW, project_id="gb-2")
    assert ("GET", "/projects?limit=100", None) not in fake.calls
    with pytest.raises(neon.NeonError, match="404"):
        neon.create_restore_point(FakeNeon(), DIRECT, "before-demo", NOW, project_id="other-1")


def test_neons_refusal_is_reported_and_main_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeNeon()
    fake.branch_answer = (422, {"message": "branches limit exceeded"})
    monkeypatch.setattr(neon, "http_call", lambda key: fake)
    environ = {"NEON_API_KEY": "napi_secret", "DATABASE_URL_DIRECT": DIRECT}

    assert neon.main(["create", "before-demo"], environ) == 1

    err = capsys.readouterr().err
    assert "422 branches limit exceeded" in err
    assert "napi_secret" not in err and "secret-pw" not in err


def test_main_needs_the_key_and_the_url() -> None:
    with pytest.raises(SystemExit) as exited:
        neon.main(["create", "before-demo"], {"DATABASE_URL": DIRECT})
    assert exited.value.code == 2


def test_main_prints_how_to_restore(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(neon, "http_call", lambda key: FakeNeon())
    environ = {"NEON_API_KEY": "napi_secret", "DATABASE_URL": POOLED}

    assert neon.main(["create", "before-demo"], environ) == 0

    out = capsys.readouterr().out
    assert "restore point: branch before-demo-" in out
    assert "napi_secret" not in out and "secret-pw" not in out


def point(id_: str, name: str, parent: str = "br-main", **extra: Any) -> dict[str, Any]:
    return branch(id_, f"before-demo-{name}", parent, **extra)


def test_earlier_points_nothing_hangs_off_are_deleted_and_the_rest_kept() -> None:
    fake, said = FakeNeon(), []
    fake.branches += [
        point("br-kept-load", "20261101T0800Z"),  # its load was kept: deleted
        point("br-rolled-back", "20261030T0800Z"),  # staging was restored from it once
        branch("br-after", "production-with-demo-20261031T0900Z", "br-rolled-back"),
        point("br-looked-at", "20261029T0800Z"),  # someone gave it a compute
        point("br-elsewhere", "20261028T0800Z", parent="br-root"),  # another branch's point
        branch("br-named-alike", "before-demo-notes", "br-main"),
    ]
    fake.computes.append("br-looked-at")

    neon.create_restore_point(fake, POOLED, "before-demo", NOW, say=said.append)

    deletes = [path for method, path, _ in fake.calls if method == "DELETE"]
    assert deletes == ["/projects/gb-2/branches/br-kept-load"]
    assert said == [
        "deleted the earlier restore point before-demo-20261101T0800Z (br-kept-load): "
        "its load was kept"
    ]


def test_a_point_is_refused_when_it_and_a_rollback_would_pass_neons_limit() -> None:
    fake = FakeNeon()
    fake.branches += [branch(f"br-dev-{n}", f"dev-{n}", "br-root") for n in range(7)]  # 9 of 10

    with pytest.raises(neon.NeonError, match="9 of Neon's 10 branches"):
        neon.create_restore_point(fake, POOLED, "before-demo", NOW, say=lambda line: None)
    assert not any(method == "POST" for method, _, _ in fake.calls)

    fake.branches.pop()  # 8: the point makes 9, a rollback 10
    neon.create_restore_point(fake, POOLED, "before-demo", NOW, say=lambda line: None)


def test_rollback_restores_from_the_point_keeps_the_old_state_and_waits_for_neon() -> None:
    fake, slept = FakeNeon(), []
    fake.branches.append(point("br-point", "20261102T0530Z"))

    found = neon.rollback(fake, POOLED, "before-demo-20261102T0530Z", NOW, sleep=slept.append)

    assert found == ("production", "production-with-demo-20261102T0530Z")
    restore = [call for call in fake.calls if call[1].endswith("/restore")]
    assert restore == [
        (
            "POST",
            "/projects/gb-2/branches/br-main/restore",
            {"source_branch_id": "br-point", "preserve_under_name": found[1]},
        )
    ]
    assert slept == [3]  # op-1 was still running once


@pytest.mark.parametrize("name", ["before-demo-20261101T0000Z", "main", "br-root"])
def test_rollback_only_takes_a_point_of_the_databases_branch(name: str) -> None:
    fake = FakeNeon()
    fake.branches.append(point("br-elsewhere", "20261101T0000Z", parent="br-root"))

    with pytest.raises(neon.NeonError, match="is not a restore point of production"):
        neon.rollback(fake, POOLED, name, NOW, sleep=lambda s: None)
    assert not any(path.endswith("/restore") for _, path, _ in fake.calls)


def test_a_failed_restore_is_reported() -> None:
    fake = FakeNeon()
    fake.branches.append(point("br-point", "20261102T0530Z"))
    original = fake.__call__

    def failing(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        answer = original(method, path, body)
        if path.endswith("/restore"):
            fake.operations["op-1"] = ["failed"]
        return answer

    with pytest.raises(neon.NeonError, match="reset_branch op-1 ended failed"):
        neon.rollback(failing, POOLED, "br-point", NOW, sleep=lambda s: None)


def test_main_rolls_back_only_with_the_hosts_confirmation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeNeon()
    fake.branches.append(point("br-point", "20261102T0530Z"))
    monkeypatch.setattr(neon, "http_call", lambda key: fake)
    monkeypatch.setattr(neon, "_sleep", lambda s: None)
    environ = {"NEON_API_KEY": "napi_secret", "DATABASE_URL": POOLED}
    host = "ep-cool-name-a1b2-pooler.ap-southeast-1.aws.neon.tech"

    assert neon.main(["rollback", "br-point"], environ) == 2
    assert f"GB_ALLOW_REMOTE_SEED={host}" in capsys.readouterr().err
    assert not any(path.endswith("/restore") for _, path, _ in fake.calls)

    assert neon.main(["rollback", "br-point"], {**environ, "GB_ALLOW_REMOTE_SEED": host}) == 0
    out = capsys.readouterr().out
    assert "production is back at br-point; the replaced state is the branch production-with" in out
    assert "napi_secret" not in out and "secret-pw" not in out
