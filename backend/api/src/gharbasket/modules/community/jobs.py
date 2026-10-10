"""Consumers of M9 Community & Kindness (spec §5): captains follow M8's verification and
suspension (§3.2). `partners.reinstated` is deliberately not consumed: the hub manager decides
whether a reinstated captain serves again."""

from gharbasket.modules.community.captains import on_partner_suspended, on_partner_verified
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform.outbox import subscribe

subscribe(partners_api.VERIFIED, on_partner_verified)
subscribe(partners_api.SUSPENDED, on_partner_suspended)
