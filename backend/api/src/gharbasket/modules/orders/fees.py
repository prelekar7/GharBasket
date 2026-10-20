"""The pre-live delivery fee on box orders (M9 §3.6 and its M4 follow-up in §11; M4 §11).

A box for a society that is not live yet (no `live_at`) pays the hub's `pre_live_box_fee_paise`;
once the society has gone live it pays nothing, also after a pause and resume. Add-on and
tiffin orders never pay it. The fee is part of the order's total, so the cut-off debits it in
the same `box_debit` entry as the food; shortage refunds and issue credits never touch it.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api


async def society_live(session: AsyncSession, society_id: uuid.UUID) -> bool:
    society = await community_api.get_society(session, society_id)
    return society is not None and society.live_at is not None


async def box_fee(session: AsyncSession, hub_id: uuid.UUID, society_id: uuid.UUID) -> int:
    """The delivery fee a box approved now pays: 0 once the society has gone live, else the
    hub's pre-live fee."""
    if await society_live(session, society_id):
        return 0
    # Imported here, not at the top: hub_ops.api loads hub_ops.packing, which imports orders.api,
    # which re-exports box_fee; at the top that is a cycle whenever orders.service loads first.
    from gharbasket.modules.hub_ops import api as hub_ops_api

    hub = await hub_ops_api.get_hub(session, hub_id)
    return 0 if hub is None else hub.pre_live_box_fee_paise
