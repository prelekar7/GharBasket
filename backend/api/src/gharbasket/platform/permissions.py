"""Permissions (TDD §4.2): which roles may do what, and in which society, hub or NGO.

Modules contribute their rules at import time, then endpoints ask for a permission:

    PERMISSIONS.allow("hub_manager", "orders:read")              # in orders/permissions.py

    @router.get("/hubs/{hub_id}/orders")
    async def hub_orders(hub_id: UUID, permit: Annotated[Permit, require("orders:read")]):
        permit.ensure(hub_id)                                     # this hub, or a global role

A grant is "role" (customer, admin) or "role:<scope id>" (captain of one society, packer at
one hub); the scope type follows from the role.

`X-Active-Role` (the app's role switcher) is optional: when sent it must be one of the caller's
roles and only that role counts. Role switching is a UX choice, not a security boundary: without
the header every role the caller holds counts.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import Depends, Header

from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.tokens import AccessClaims

ScopeType = Literal["society", "hub", "ngo"]

# Where each role applies. None: everywhere (admin) or only to the user's own things (customer).
ROLE_SCOPE: dict[str, ScopeType | None] = {
    "customer": None,
    "captain": "society",
    "kitchen_partner": "hub",
    "rider": "hub",
    "packer": "hub",
    "hub_manager": "hub",
    "franchise_owner": "hub",
    "ngo_coordinator": "ngo",
    "admin": None,
}


@dataclass(frozen=True)
class Grant:
    role: str
    scope_id: uuid.UUID | None

    @property
    def scope_type(self) -> ScopeType | None:
        return ROLE_SCOPE.get(self.role)

    @classmethod
    def parse(cls, value: str) -> "Grant | None":
        role, _, scope = value.partition(":")
        if role not in ROLE_SCOPE:
            return None
        if not scope:
            return cls(role, None) if ROLE_SCOPE[role] is None else None
        if ROLE_SCOPE[role] is None:
            return None
        try:
            return cls(role, uuid.UUID(scope))
        except ValueError:
            return None

    def __str__(self) -> str:
        return f"{self.role}:{self.scope_id}" if self.scope_id else self.role


class PermissionRegistry:
    def __init__(self) -> None:
        self._roles_by_permission: dict[str, set[str]] = defaultdict(set)

    def allow(self, role: str, *permissions: str) -> None:
        if role not in ROLE_SCOPE:
            raise ValueError(f"unknown role {role!r}")
        for permission in permissions:
            self._roles_by_permission[permission].add(role)

    def roles_for(self, permission: str) -> frozenset[str]:
        return frozenset(self._roles_by_permission.get(permission, set()))


PERMISSIONS = PermissionRegistry()


def forbidden(detail: str = "You do not have access to this.") -> ProblemError:
    return ProblemError(403, "auth.forbidden", detail)


@dataclass(frozen=True)
class Permit:
    """Proof that the caller holds `permission` through one or more grants."""

    principal: AccessClaims
    permission: str
    grants: tuple[Grant, ...]

    def allows(self, scope_id: uuid.UUID) -> bool:
        """True if a grant covers this society/hub/NGO: an unscoped role, or the same scope id."""
        return any(grant.scope_id is None or grant.scope_id == scope_id for grant in self.grants)

    def ensure(self, scope_id: uuid.UUID) -> None:
        if not self.allows(scope_id):
            raise forbidden()


def active_grants(principal: AccessClaims, active_role: str | None) -> tuple[Grant, ...]:
    grants = tuple(g for g in (Grant.parse(r) for r in principal.roles) if g is not None)
    if active_role is None:
        return grants
    chosen = Grant.parse(active_role)
    if chosen is None or chosen not in grants:
        raise ProblemError(
            403, "auth.role_not_granted", "The selected role is not one of your roles."
        )
    return (chosen,)


def require(permission: str) -> Any:
    """A dependency: 403 `auth.forbidden` unless a usable grant carries `permission`."""

    async def check(
        principal: CurrentPrincipal,
        x_active_role: Annotated[str | None, Header(max_length=100)] = None,
    ) -> Permit:
        usable = active_grants(principal, x_active_role)
        allowed_roles = PERMISSIONS.roles_for(permission)
        grants = tuple(g for g in usable if g.role in allowed_roles)
        if not grants:
            raise forbidden()
        return Permit(principal, permission, grants)

    return Depends(check)
