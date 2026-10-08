"""Fixtures for orders tests: the seed world (people, societies, households, catalog, prices)."""

# Shared with the identity and catalog tests (pytest finds fixtures imported into a conftest).
from ..catalog.conftest import world  # noqa: F401
from ..identity.conftest import clients, google  # noqa: F401
from ..subscriptions.conftest import delivery  # noqa: F401
