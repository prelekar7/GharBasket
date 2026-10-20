"""Training modules and their quizzes (spec §3.2).

The modules a person sees are those required for the kinds of her open application and her
partner records. Quizzes are scored on the server; the answers never leave it. A pass is kept for
good, whatever later attempts score, and counts for every version of the module.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners.models import (
    OPEN_STATUSES,
    REQUIRED_TRAININGS,
    Application,
    Partner,
    TrainingAttempt,
    TrainingModule,
)
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import DEFAULT, Language

IST = ZoneInfo("Asia/Kolkata")
ATTEMPTS_PER_DAY = 5  # per module and IST day (§3.2)
SCORE_STEP = Decimal("0.001")


@dataclass(frozen=True)
class State:
    """A person's standing in one module, over all its versions."""

    status: str  # todo, passed, failed
    best_score: Decimal | None
    passed_at: datetime | None


async def kinds_of(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """The kinds of her open application and her partner records (not those she left)."""
    kinds: list[str] = []
    open_kind = (
        await session.execute(
            select(Application.kind).where(
                Application.user_id == user_id, Application.status.in_(OPEN_STATUSES)
            )
        )
    ).scalar_one_or_none()
    if open_kind is not None:
        kinds.append(open_kind)
    partner_kinds = await session.execute(
        select(Partner.kind)
        .where(Partner.user_id == user_id, Partner.status != "left")
        .order_by(Partner.kind)
    )
    kinds.extend(kind for kind in partner_kinds.scalars() if kind not in kinds)
    return kinds


def required_codes(kinds: list[str]) -> list[str]:
    """The modules the kinds need, in their required order, each once."""
    codes: list[str] = []
    for kind in kinds:
        codes.extend(code for code in REQUIRED_TRAININGS[kind] if code not in codes)
    return codes


async def current_modules(session: AsyncSession, codes: list[str]) -> dict[str, TrainingModule]:
    """The current (highest active) version of each module."""
    if not codes:
        return {}
    rows = await session.execute(
        select(TrainingModule)
        .where(TrainingModule.code.in_(codes), TrainingModule.active)
        .order_by(TrainingModule.code, TrainingModule.version)
    )
    return {module.code: module for module in rows.scalars()}  # the last version wins


async def states_for_users(
    session: AsyncSession, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, State]]:
    """Each user's state per module she has attempted; modules never tried are `todo`."""
    if not user_ids:
        return {}
    rows = await session.execute(
        select(
            TrainingAttempt.user_id,
            TrainingAttempt.module_code,
            func.max(TrainingAttempt.score),
            func.min(TrainingAttempt.at).filter(TrainingAttempt.passed),
        )
        .where(TrainingAttempt.user_id.in_(set(user_ids)))
        .group_by(TrainingAttempt.user_id, TrainingAttempt.module_code)
    )
    states: dict[uuid.UUID, dict[str, State]] = {}
    for user_id, code, best, passed_at in rows.all():
        status = "passed" if passed_at is not None else "failed"
        states.setdefault(user_id, {})[code] = State(status, best, passed_at)
    return states


def _text(module: TrainingModule, lang: Language, field: str) -> Any:
    """A text in `lang`, else in English (§3.2)."""
    texts = module.texts
    return (texts.get(lang) or {}).get(field) or texts[DEFAULT][field]


def localised(module: TrainingModule, lang: Language) -> dict[str, Any]:
    """The module's texts and questions in `lang`, without the answers."""
    videos = module.video_url or {}
    return {
        "title": _text(module, lang, "title"),
        "summary": _text(module, lang, "summary"),
        "video_url": videos.get(lang) or videos.get(DEFAULT),
        "questions": [
            {"id": q["id"], "text": q["text"], "options": list(q["options"])}
            for q in _text(module, lang, "questions")
        ],
    }


def not_found() -> ProblemError:
    return ProblemError(404, "partners.training_not_found", "No such training for you.")


@dataclass(frozen=True)
class Result:
    code: str
    score: Decimal
    correct: int
    total: int
    passed: bool
    status: str


def _score(module: TrainingModule, answers: list[dict[str, Any]]) -> tuple[int, int]:
    """(correct, total); 422 `partners.answers_invalid` unless every question is answered once
    with an option that exists."""
    questions = {q["id"]: len(q["options"]) for q in module.texts[DEFAULT]["questions"]}
    given = [answer["question_id"] for answer in answers]
    invalid = (
        len(given) != len(set(given))
        or set(given) != set(questions)
        or any(not 0 <= a["option"] < questions[a["question_id"]] for a in answers)
    )
    if invalid:
        raise ProblemError(
            422,
            "partners.answers_invalid",
            "Answer every question once, with one of its options.",
        )
    correct = sum(module.answers.get(a["question_id"]) == a["option"] for a in answers)
    return correct, len(questions)


async def _attempts_today(session: AsyncSession, user_id: uuid.UUID, code: str) -> int:
    today = datetime.now(IST).date()
    start = datetime.combine(today, time(), IST).astimezone(UTC)
    return (
        await session.execute(
            select(func.count()).where(
                TrainingAttempt.user_id == user_id,
                TrainingAttempt.module_code == code,
                TrainingAttempt.at >= start,
                TrainingAttempt.at < start + timedelta(days=1),
            )
        )
    ).scalar_one()


async def complete(
    session: AsyncSession,
    user_id: uuid.UUID,
    code: str,
    *,
    version: int,
    answers: list[dict[str, Any]],
) -> Result:
    """Score one attempt and record it (§3.2). The caller then checks whether the pass
    completes an approved application."""
    if code not in required_codes(await kinds_of(session, user_id)):
        raise not_found()
    module = (await current_modules(session, [code])).get(code)
    if module is None:
        raise not_found()
    if version != module.version:
        raise ProblemError(
            409,
            "partners.training_version_stale",
            "This module has changed; reload it.",
            extra={"version": module.version},
        )
    if await _attempts_today(session, user_id, code) >= ATTEMPTS_PER_DAY:
        raise ProblemError(
            429,
            "partners.training_attempts_exceeded",
            f"At most {ATTEMPTS_PER_DAY} attempts a day; try again tomorrow.",
        )
    correct, total = _score(module, answers)
    score = (Decimal(correct) / Decimal(total)).quantize(SCORE_STEP, ROUND_HALF_UP)
    passed = score >= module.pass_score
    session.add(
        TrainingAttempt(
            user_id=user_id,
            module_code=code,
            module_version=module.version,
            score=score,
            passed=passed,
            answers=answers,
            at=datetime.now(UTC),
        )
    )
    await session.flush()
    state = (await states_for_users(session, [user_id]))[user_id][code]
    return Result(code, score, correct, total, passed, state.status)


async def modules_for(
    session: AsyncSession, user_id: uuid.UUID
) -> list[tuple[TrainingModule, State]]:
    """The modules she needs, in required order, with her state in each."""
    codes = required_codes(await kinds_of(session, user_id))
    modules = await current_modules(session, codes)
    states = (await states_for_users(session, [user_id])).get(user_id, {})
    todo = State("todo", None, None)
    return [(modules[code], states.get(code, todo)) for code in codes if code in modules]


async def save_module(session: AsyncSession, data: dict[str, Any]) -> tuple[TrainingModule, bool]:
    """Create or refresh one module version from seed data: (module, created)."""
    module = await session.get(TrainingModule, (data["code"], data["version"]))
    created = module is None
    if module is None:
        module = TrainingModule(code=data["code"], version=data["version"])
        session.add(module)
    module.for_kinds = list(data["for_kinds"])
    module.required = data.get("required", True)
    module.minutes = data["minutes"]
    module.texts = data["texts"]
    module.video_url = data.get("video_url")
    module.answers = data["answers"]
    module.pass_score = Decimal(str(data["pass_score"]))
    module.active = data.get("active", True)
    await session.flush()
    return module, created


async def attempts_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[TrainingAttempt]:
    rows = await session.execute(
        select(TrainingAttempt)
        .where(TrainingAttempt.user_id == user_id)
        .order_by(TrainingAttempt.at)
    )
    return list(rows.scalars())
