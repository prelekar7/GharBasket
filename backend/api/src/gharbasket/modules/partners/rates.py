"""The rate card (spec §3.6): who earns what for which event, per hub.

M8 decides the amount, M5 records it. Every hub starts with the owner's rates (2026-10-09), in
`DEFAULTS`; an admin changes them per hub with dated rows in `partners.rate_cards`. The rate of an
order is the one valid on its delivery date, so a later change never reprices work already done.
"""

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners.kitchens import today
from gharbasket.modules.partners.models import BASIS_OF_KIND, KINDS, RateCard
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError

# Paise per unit: a portion, a bag, a dispatched order, a packed order.
DEFAULTS: dict[str, int] = {"kitchen": 6_000, "captain": 600, "rider": 800, "packer": 400}
DEFAULT_FROM = date(2026, 10, 9)  # the day the owner decided them


@dataclass(frozen=True)
class Rate:
    kind: str
    basis: str
    amount_paise: int
    valid_from: date


def invalid(detail: str) -> ProblemError:
    return ProblemError(422, "partners.rate_invalid", detail)


def _dump(rates: list[Rate]) -> list[dict[str, object]]:
    return [
        {
            "kind": r.kind,
            "basis": r.basis,
            "amount_paise": r.amount_paise,
            "valid_from": r.valid_from.isoformat(),
        }
        for r in rates
    ]


async def _rows(session: AsyncSession, hub_id: uuid.UUID) -> list[RateCard]:
    rows = await session.execute(
        select(RateCard)
        .where(RateCard.hub_id == hub_id)
        .order_by(RateCard.kind, RateCard.valid_from)
        .execution_options(populate_existing=True)  # an upsert above bypasses the identity map
    )
    return list(rows.scalars())


async def rate_card(session: AsyncSession, hub_id: uuid.UUID, on: date) -> list[Rate]:
    """The rates in force on `on` (the default where the hub set none yet), then the ones the
    hub scheduled for later, by kind and date."""
    rows = await _rows(session, hub_id)
    out: list[Rate] = []
    for kind in KINDS:
        basis = BASIS_OF_KIND[kind]
        mine = [r for r in rows if r.kind == kind and r.basis == basis]
        past = [r for r in mine if r.valid_from <= on]
        if past:
            latest = max(past, key=lambda r: r.valid_from)
            out.append(Rate(kind, basis, latest.amount_paise, latest.valid_from))
        else:
            out.append(Rate(kind, basis, DEFAULTS[kind], DEFAULT_FROM))
        out += [Rate(kind, basis, r.amount_paise, r.valid_from) for r in mine if r.valid_from > on]
    return out


async def rate_on(session: AsyncSession, hub_id: uuid.UUID, kind: str, on: date) -> int:
    """Paise per unit for `kind` at the hub on a delivery date."""
    found = await session.scalar(
        select(RateCard.amount_paise)
        .where(
            RateCard.hub_id == hub_id,
            RateCard.kind == kind,
            RateCard.basis == BASIS_OF_KIND[kind],
            RateCard.valid_from <= on,
        )
        .order_by(RateCard.valid_from.desc())
        .limit(1)
    )
    return DEFAULTS[kind] if found is None else int(found)


async def replace(
    session: AsyncSession,
    hub_id: uuid.UUID,
    rates: list[Rate],
    *,
    by_user_id: uuid.UUID,
) -> list[Rate]:
    """Set dated rates (audit-logged): a row per (kind, basis, valid_from), replacing the amount
    when that date already has one. Today or later only; the answer is the hub's card."""
    first = today()
    seen: set[tuple[str, str, date]] = set()
    for rate in rates:
        if BASIS_OF_KIND.get(rate.kind) != rate.basis:
            raise invalid(f"A {rate.kind} earns per {BASIS_OF_KIND.get(rate.kind)}.")
        if rate.valid_from < first:
            raise invalid("A rate starts today or later.")
        if (rate.kind, rate.basis, rate.valid_from) in seen:
            raise invalid("Each kind and date appears once.")
        seen.add((rate.kind, rate.basis, rate.valid_from))
    before = await rate_card(session, hub_id, first)
    for rate in rates:
        await session.execute(
            insert(RateCard)
            .values(
                hub_id=hub_id,
                kind=rate.kind,
                basis=rate.basis,
                amount_paise=rate.amount_paise,
                valid_from=rate.valid_from,
                created_by=by_user_id,
            )
            .on_conflict_do_update(
                index_elements=["hub_id", "kind", "basis", "valid_from"],
                set_={"amount_paise": rate.amount_paise, "created_by": by_user_id},
            )
        )
    await audit.record(
        session,
        actor_user_id=by_user_id,
        action="partners.rates_set",
        entity_type="hub",
        entity_id=str(hub_id),
        before={"rates": _dump(before)},
        after={"rates": _dump(rates)},
    )
    await session.flush()
    return await rate_card(session, hub_id, first)
