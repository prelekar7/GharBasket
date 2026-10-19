"""Training (M8 §3.2): the modules a person needs in her language, server-side scoring, a pass
kept for good, the daily attempt limit and a stale version."""

import json
import uuid
from datetime import timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.partners import training
from gharbasket.modules.partners.models import TrainingAttempt, TrainingModule
from gharbasket.tools.seed import TRAININGS_SEED

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import MEENA, PRIYA, as_user
from .test_applications import apply, code, post

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("partners_world")]

ANSWERS = {m["code"]: m["answers"] for m in json.loads(TRAININGS_SEED.read_text())["modules"]}


def answers(module: str, *, wrong: int = 0) -> list[dict[str, Any]]:
    """Every question answered; the first `wrong` of them wrongly."""
    out = []
    for n, (question, option) in enumerate(sorted(ANSWERS[module].items())):
        out.append({"question_id": question, "option": (option + 1) % 3 if n < wrong else option})
    return out


async def complete(
    api: httpx2.AsyncClient, auth: dict[str, str], module: str, body: dict[str, Any]
) -> httpx2.Response:
    return await post(api, f"/v1/trainings/{module}/complete", auth, body)


async def test_the_modules_follow_her_kinds_in_her_language_without_answers(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    meena, _ = await as_user(api, google, MEENA)

    before = await api.get("/v1/trainings", headers=priya)
    await apply(api, priya, kind="kitchen")
    english = await api.get("/v1/trainings", headers=priya)
    hindi = await api.get("/v1/trainings", headers={**priya, "Accept-Language": "hi-IN,hi;q=0.9"})
    captain = await api.get("/v1/trainings", headers={**meena, "Accept-Language": "mr"})

    assert before.status_code == 200 and before.json() == {"items": []}
    items = english.json()["items"]
    assert [m["code"] for m in items] == ["app_basics", "hygiene", "kitchen_ops"]
    hygiene = items[1]
    assert (hygiene["title"], hygiene["version"], hygiene["minutes"]) == ("Kitchen hygiene", 1, 8)
    assert hygiene["for_kinds"] == ["kitchen", "packer"] and hygiene["pass_score"] == 0.8
    assert (hygiene["status"], hygiene["best_score"], hygiene["passed_at"]) == ("todo", None, None)
    assert hygiene["video_url"] is None and len(hygiene["questions"]) == 5
    assert set(hygiene["questions"][0]) == {"id", "text", "options"}
    assert "answers" not in english.text and '"answer' not in english.text
    assert english.headers["Cache-Control"] == "no-store"
    assert hindi.headers["Content-Language"] == "hi"
    assert hindi.json()["items"][1]["title"] == "रसोई की स्वच्छता"
    # Questions not yet written in Hindi fall back to English.
    assert hindi.json()["items"][1]["questions"] == hygiene["questions"]
    # A verified captain partner sees her kind's modules.
    assert [m["code"] for m in captain.json()["items"]] == ["app_basics", "captain_handover"]
    assert captain.json()["items"][1]["title"] == "सोसायटीतील हस्तांतरण"


async def test_scoring_is_on_the_server_and_a_pass_is_kept_for_good(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    application = (await apply(api, priya, kind="packer")).json()

    failed = await complete(
        api, priya, "hygiene", {"version": 1, "answers": answers("hygiene", wrong=2)}
    )
    passed = await complete(
        api, priya, "hygiene", {"version": 1, "answers": answers("hygiene", wrong=1)}
    )
    later = await complete(
        api, priya, "hygiene", {"version": 1, "answers": answers("hygiene", wrong=5)}
    )
    listed = await api.get("/v1/trainings", headers=priya)
    mine = await api.get("/v1/partner-applications/me", headers=priya)

    assert failed.status_code == 200, failed.text
    assert failed.json() == {
        "code": "hygiene",
        "score": 0.6,
        "correct": 3,
        "total": 5,
        "passed": False,
        "status": "failed",
        "application": {"id": application["id"], "status": "applied", "next_step": "documents"},
    }
    assert passed.json()["score"] == 0.8 and passed.json()["passed"] is True
    assert passed.json()["status"] == "passed"
    assert later.json()["passed"] is False and later.json()["status"] == "passed"
    hygiene = next(m for m in listed.json()["items"] if m["code"] == "hygiene")
    assert (hygiene["status"], hygiene["best_score"]) == ("passed", 0.8)
    assert hygiene["passed_at"] is not None
    assert {"code": "hygiene", "status": "passed"} in mine.json()["requirements"]["trainings"]
    section = await partners_api.export_for_user(db_session, priya_id)
    assert [(a["module_code"], a["passed"]) for a in section["training_attempts"]] == [
        ("hygiene", False),
        ("hygiene", True),
        ("hygiene", False),
    ]


async def test_answers_versions_modules_and_the_daily_limit_are_checked(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    await apply(api, priya, kind="packer")
    right = answers("packing")
    await db_session.commit()

    stale = await complete(api, priya, "packing", {"version": 2, "answers": right})
    missing = await complete(api, priya, "packing", {"version": 1, "answers": right[1:]})
    repeated = await complete(
        api, priya, "packing", {"version": 1, "answers": [*right[1:], right[1]]}
    )
    out_of_range = await complete(
        api,
        priya,
        "packing",
        {"version": 1, "answers": [*right[1:], {"question_id": "q1", "option": 3}]},
    )
    not_hers = await complete(api, priya, "kitchen_ops", {"version": 1, "answers": right})
    unknown = await complete(api, priya, "juggling", {"version": 1, "answers": right})

    assert code(stale) == (409, "partners.training_version_stale")
    assert stale.json()["version"] == 1
    assert (
        code(missing) == code(repeated) == code(out_of_range) == (422, "partners.answers_invalid")
    )
    assert code(not_hers) == code(unknown) == (404, "partners.training_not_found")

    wrong = {"version": 1, "answers": answers("packing", wrong=5)}
    tries = [await complete(api, priya, "packing", wrong) for _ in range(5)]
    await db_session.commit()
    sixth = await complete(api, priya, "packing", wrong)
    other_module = await complete(
        api, priya, "app_basics", wrong | {"answers": answers("app_basics")}
    )
    # The limit is per IST day: yesterday's attempts do not count today.
    await db_session.execute(
        update(TrainingAttempt)
        .where(TrainingAttempt.module_code == "packing")
        .values(at=TrainingAttempt.at - timedelta(days=1))
    )
    await db_session.commit()
    next_day = await complete(api, priya, "packing", {"version": 1, "answers": right})

    assert [t.status_code for t in tries] == [200] * 5
    assert code(sixth) == (429, "partners.training_attempts_exceeded")
    assert other_module.status_code == 200
    assert next_day.status_code == 200 and next_day.json()["passed"] is True


async def test_a_new_version_is_the_current_one_and_an_old_pass_still_counts(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    await apply(api, priya, kind="packer")
    await complete(api, priya, "packing", {"version": 1, "answers": answers("packing")})
    v1 = await db_session.get(TrainingModule, ("packing", 1))
    assert v1 is not None
    await training.save_module(
        db_session,
        {
            "code": "packing",
            "version": 2,
            "for_kinds": v1.for_kinds,
            "minutes": 7,
            "pass_score": 1.0,
            "texts": v1.texts,
            "answers": v1.answers,
        },
    )
    await db_session.commit()

    listed = await api.get("/v1/trainings", headers=priya)
    old = await complete(api, priya, "packing", {"version": 1, "answers": answers("packing")})

    packing = next(m for m in listed.json()["items"] if m["code"] == "packing")
    assert (packing["version"], packing["minutes"], packing["status"]) == (2, 7, "passed")
    assert code(old) == (409, "partners.training_version_stale")
    states = await training.states_for_users(db_session, [priya_id, uuid.uuid4()])
    assert states[priya_id]["packing"].status == "passed"
