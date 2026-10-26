"""A hub's unit-economics assumptions (spec §3.4): the saved values (the default for a key the hub
never changed), their version for the ETag, and the PUT that validates, versions, audits and
records the history.

A hub's saved values are rows of `analytics.assumptions` with its `hub_id`, one per key it changed;
the defaults are the rows with a null `hub_id`. The PUT writes only the keys that changed, so a
hub that never changed a key follows the default. Money is integer paise and rates are basis points.
"""

import uuid
from collections import defaultdict
from collections.abc import Mapping

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics.economics_schemas import (
    AssumptionsOut,
    ChangedByOut,
    ChangeOut,
    HistoryOut,
    HubAssumptionsOut,
)
from gharbasket.modules.analytics.models import (
    ASSUMPTION_KEYS,
    DEFAULT_ASSUMPTIONS,
    Assumption,
    AssumptionChange,
    AssumptionVersion,
)
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError

HISTORY_LIMIT = 20
FIRST_VERSION = 1  # a hub that never saved anything: the defaults
# Allowed range per key (spec §3.4): money in paise, rates in basis points.
RANGES: dict[str, tuple[int, int]] = {
    "aov_paise": (10_000, 500_000),
    "margin_bp": (0, 6_000),
    "delivery_paise": (0, 20_000),
    "packing_paise": (0, 20_000),
    "captain_paise": (0, 5_000),
    "payment_fee_bp": (0, 500),
    "cac_paise": (0, 50_000),
}
# The delivery fee is not an assumption but a what-if can set it, in the delivery cost's range.
DELIVERY_FEE_RANGE = RANGES["delivery_paise"]


def problems(values: Mapping[str, int | None]) -> list[dict[str, str]]:
    """`errors[]` for the values out of range (spec §3.4); null is not given."""
    errors: list[dict[str, str]] = []
    for field, value in values.items():
        low, high = DELIVERY_FEE_RANGE if field == "delivery_fee_paise" else RANGES[field]
        if value is not None and not low <= value <= high:
            unit = "basis points" if field.endswith("_bp") else "paise"
            errors.append({"field": field, "detail": f"Must be from {low} to {high} {unit}."})
    return errors


def ensure_valid(values: Mapping[str, int | None]) -> None:
    errors = problems(values)
    if errors:
        raise ProblemError(
            422,
            "analytics.assumptions_invalid",
            "Some values are outside the allowed range.",
            errors=errors,
        )


async def saved(session: AsyncSession, hub_id: uuid.UUID) -> dict[str, int]:
    """The hub's values: its own rows over the defaults (and the built-in ones for a default row
    that is missing)."""
    rows = await session.execute(
        select(Assumption.hub_id, Assumption.key, Assumption.value).where(
            (Assumption.hub_id == hub_id) | Assumption.hub_id.is_(None)
        )
    )
    values = dict(DEFAULT_ASSUMPTIONS)
    own: dict[str, int] = {}
    for row_hub, key, value in rows.all():
        if row_hub is None:
            values[key] = value
        else:
            own[key] = value
    return values | own


async def version(session: AsyncSession, hub_id: uuid.UUID) -> int:
    current = await session.scalar(
        select(AssumptionVersion.version).where(AssumptionVersion.hub_id == hub_id)
    )
    return current or FIRST_VERSION


async def history(session: AsyncSession, hub_id: uuid.UUID) -> list[HistoryOut]:
    """The newest 20 saves, newest first; the keys of one PUT share a version."""
    newest = (
        select(AssumptionChange.version)
        .where(AssumptionChange.hub_id == hub_id)
        .distinct()
        .order_by(AssumptionChange.version.desc())
        .limit(HISTORY_LIMIT)
    )
    rows = (
        (
            await session.execute(
                select(AssumptionChange)
                .where(AssumptionChange.hub_id == hub_id, AssumptionChange.version.in_(newest))
                .order_by(AssumptionChange.version.desc())
            )
        )
        .scalars()
        .all()
    )
    saves: dict[int, list[AssumptionChange]] = defaultdict(list)
    for row in rows:
        saves[row.version].append(row)
    names = await identity_api.user_names(
        session, {row.by_user_id for row in rows if row.by_user_id is not None}
    )
    order = {key: i for i, key in enumerate(ASSUMPTION_KEYS)}
    return [
        HistoryOut(
            at=min(r.at for r in changes),
            by=ChangedByOut(
                name=names.get(changes[0].by_user_id) if changes[0].by_user_id else None
            ),
            changes=[
                ChangeOut.model_validate({"key": r.key, "from": r.from_value, "to": r.to_value})
                for r in sorted(changes, key=lambda r: order[r.key])
            ],
        )
        for _, changes in sorted(saves.items(), reverse=True)
    ]


async def view(session: AsyncSession, hub_id: uuid.UUID) -> HubAssumptionsOut:
    return HubAssumptionsOut(
        hub_id=hub_id,
        assumptions=AssumptionsOut(**await saved(session, hub_id)),
        version=await version(session, hub_id),
        history=await history(session, hub_id),
    )


async def replace(
    session: AsyncSession,
    hub_id: uuid.UUID,
    new: Mapping[str, int],
    expected: int,
    user_id: uuid.UUID,
) -> HubAssumptionsOut:
    """Save the hub's values if `expected` is its current version (412 `request.precondition_failed`
    otherwise). Each changed key adds a history row and the version moves up by one; a save that
    changes nothing is answered with the current values and no new version. Audit-logged."""
    ensure_valid(new)
    # The version row is the lock: two writers holding the same version cannot both succeed.
    await session.execute(
        pg_insert(AssumptionVersion).values(hub_id=hub_id).on_conflict_do_nothing()
    )
    row = (
        await session.execute(
            select(AssumptionVersion).where(AssumptionVersion.hub_id == hub_id).with_for_update()
        )
    ).scalar_one()
    if row.version != expected:
        raise ProblemError(
            412,
            "request.precondition_failed",
            "The assumptions changed since you read them. Reload and try again.",
        )
    before = await saved(session, hub_id)
    changed = {key: value for key, value in new.items() if before[key] != value}
    if not changed:
        return await view(session, hub_id)

    row.version += 1
    own = {
        a.key: a
        for a in (await session.execute(select(Assumption).where(Assumption.hub_id == hub_id)))
        .scalars()
        .all()
    }
    for key in ASSUMPTION_KEYS:
        if key not in changed:
            continue
        if key in own:
            own[key].value = changed[key]
            own[key].updated_by = user_id
            own[key].updated_at = func.now()
        else:
            session.add(Assumption(hub_id=hub_id, key=key, value=changed[key], updated_by=user_id))
        session.add(
            AssumptionChange(
                hub_id=hub_id,
                key=key,
                from_value=before[key],
                to_value=changed[key],
                by_user_id=user_id,
                version=row.version,
            )
        )
    await session.flush()
    await audit.record(
        session,
        actor_user_id=user_id,
        action="analytics.assumptions_changed",
        entity_type="analytics.assumptions",
        entity_id=str(hub_id),
        before={key: before[key] for key in changed} | {"version": expected},
        after=dict(changed) | {"version": row.version},
    )
    return await view(session, hub_id)
