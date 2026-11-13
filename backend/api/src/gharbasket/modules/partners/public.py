"""The public cook page (spec §3.5): `/cook/<handle>`'s data, for anyone, only with her consent.

It is the card's public sibling with less on it: the hub's name instead of her `area`, and no
`portions_served` (with the rate card it gives her pay) or `partner_id`. The handle is minted and
cleared in `kitchens.set_public`; leaving and erasure clear it too.
"""

import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import kitchens, training
from gharbasket.modules.partners.models import (
    REQUIRED_TRAININGS,
    Assignment,
    AssignmentOrder,
    Kitchen,
    Partner,
    TrainingAttempt,
)
from gharbasket.platform.locale import Language, localised

HANDLE = re.compile(f"[{kitchens.HANDLE_ALPHABET}]{{{kitchens.HANDLE_LENGTH}}}")
DISH_DAYS = 60
MAX_DISHES = 6


async def cook_rows(session: AsyncSession, handle: str) -> tuple[Partner, Kitchen] | None:
    """The kitchen behind a live handle, while she is active or paused; None otherwise (a
    suspended kitchen keeps her handle and is back on reinstatement)."""
    if not HANDLE.fullmatch(handle):
        return None
    row = (
        await session.execute(
            select(Partner, Kitchen)
            .join(Kitchen, Kitchen.partner_id == Partner.id)
            .where(Kitchen.public_handle == handle, Partner.kind == "kitchen")
        )
    ).first()
    if row is None or row[0].status not in ("active", "paused"):
        return None
    return row[0], row[1]


async def trainings(
    session: AsyncSession, user_id: uuid.UUID, lang: Language
) -> list[tuple[str, str]]:
    """Her passed modules of the kitchen track (the current version's `for_kinds` has `kitchen`)
    as (code, title): the required ones in their order first, titled in `lang` (else English).
    Never a captain's, rider's or packer's own module, which would tell the internet more."""
    passed = await session.scalars(
        select(TrainingAttempt.module_code)
        .where(TrainingAttempt.user_id == user_id, TrainingAttempt.passed)
        .distinct()
    )
    modules = await training.current_modules(session, list(passed))
    required = REQUIRED_TRAININGS["kitchen"]
    codes = sorted(
        (code for code, module in modules.items() if "kitchen" in module.for_kinds),
        key=lambda code: (required.index(code) if code in required else len(required), code),
    )
    return [(code, training.localised(modules[code], lang)["title"]) for code in codes]


async def dishes(session: AsyncSession, partner_id: uuid.UUID, lang: Language) -> list[str]:
    """The names of the tiffins of her assignments delivered in the last 60 IST days, most
    portions first."""
    first = kitchens.today() - timedelta(days=DISH_DAYS - 1)
    rows = (
        await session.execute(
            select(AssignmentOrder.order_id, AssignmentOrder.portions)
            .join(Assignment, Assignment.id == AssignmentOrder.assignment_id)
            .where(
                Assignment.partner_id == partner_id,
                Assignment.on_date >= first,
                AssignmentOrder.status == "delivered",
            )
        )
    ).all()
    products = await orders_api.tiffin_products(session, [order_id for order_id, _ in rows])
    portions: dict[uuid.UUID, int] = defaultdict(int)
    for order_id, count in rows:
        if order_id in products:
            portions[products[order_id]] += count
    found = await catalog_api.line_products(session, list(portions))
    names = {pid: localised(product.names, lang) for pid, product in found.items()}
    ranked = sorted(names, key=lambda pid: (-portions[pid], names[pid]))
    return [names[pid] for pid in ranked[:MAX_DISHES]]


async def since_for_user(session: AsyncSession, user_id: uuid.UUID) -> datetime | None:
    """When her public page went on, for the data export; None while it is off."""
    return await session.scalar(
        select(Kitchen.public_since)
        .join(Partner, Partner.id == Kitchen.partner_id)
        .where(Partner.user_id == user_id, Partner.kind == "kitchen")
    )
