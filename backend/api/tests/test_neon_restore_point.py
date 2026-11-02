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


class FakeNeon:
    """Two projects; the second owns the endpoint, on its branch `production`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.branch_answer: tuple[int, dict[str, Any]] = (
            201,
            {"branch": {"id": "br-new-1", "name": "before-demo-20261102T0530Z"}},
        )

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
        if path == "/projects/gb-2/branches/br-main":
            return 200, {"branch": {"id": "br-main", "name": "production"}}
        if method == "POST" and path == "/projects/gb-2/branches":
            return self.branch_answer
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
    assert "neonctl branches restore br-main br-new-1" in help_text
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

    assert neon.main(["before-demo"], environ) == 1

    err = capsys.readouterr().err
    assert "422 branches limit exceeded" in err
    assert "napi_secret" not in err and "secret-pw" not in err


def test_main_needs_the_key_and_the_url() -> None:
    with pytest.raises(SystemExit) as exited:
        neon.main(["before-demo"], {"DATABASE_URL": DIRECT})
    assert exited.value.code == 2


def test_main_prints_how_to_restore(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(neon, "http_call", lambda key: FakeNeon())
    environ = {"NEON_API_KEY": "napi_secret", "DATABASE_URL": POOLED}

    assert neon.main(["before-demo"], environ) == 0

    out = capsys.readouterr().out
    assert "restore point: branch before-demo-" in out
    assert "napi_secret" not in out and "secret-pw" not in out
