"""M1's hub roles for kitchens, riders and packers, from M8's partner events (spec §5 consumed;
M8 §3.3; ADR-0026 §4).

`partners.verified` grants `kitchen_partner:<hub>`, `rider:<hub>` or `packer:<hub>`;
`partners.suspended` marks it `suspended` (it leaves the access token at the next refresh) and
`partners.reinstated` marks it `verified` again. Captains get nothing here: their role follows
M9's appointments. Each event is handled by reading the partner's current state through
`partners.api`, so a redelivered or late event can never undo a newer change.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import User, UserRole
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform import audit
from gharbasket.platform.models import OutboxEvent

ROLE_OF_KIND = {"kitchen": "kitchen_partner", "rider": "rider", "packer": "packer"}


async def sync_partner_role(session: AsyncSession, event: OutboxEvent) -> None:
    try:
        partner_id = uuid.UUID(str(event.payload["partner_id"]))
    except (KeyError, ValueError):
        return
    partner = await partners_api.get_partner(session, partner_id)
    if partner is None or partner.kind not in ROLE_OF_KIND:
        return
    if await session.get(User, partner.user_id) is None:
        return
    role = ROLE_OF_KIND[partner.kind]
    status = "verified" if partner.status in ("active", "paused") else "suspended"
    grant = (
        await session.execute(
            select(UserRole).where(
                UserRole.user_id == partner.user_id,
                UserRole.role == role,
                UserRole.scope_id == partner.hub_id,
            )
        )
    ).scalar_one_or_none()
    if grant is not None and grant.status == status:
        return
    if grant is None and status == "suspended":
        return  # nothing granted, nothing to suspend
    before = None if grant is None else {"status": grant.status}
    if grant is None:
        grant = UserRole(
            user_id=partner.user_id, role=role, scope_type="hub", scope_id=partner.hub_id
        )
        session.add(grant)
    grant.status = status
    if status == "verified":
        grant.verified_at = datetime.now(UTC)
    await session.flush()
    await audit.record(
        session,
        actor_user_id=None,
        action="roles.granted" if status == "verified" else "roles.suspended",
        entity_type="user",
        entity_id=str(partner.user_id),
        before=before,
        after={"role": role, "scope_id": str(partner.hub_id), "status": status},
    )
