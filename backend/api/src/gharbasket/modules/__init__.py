"""Business modules: one folder per module, one Postgres schema per module (ADR-0019).

A module may import another module only through its `api.py` facade (import-linter enforces this).
Each folder has the same files:

- `api.py`      the facade: the only thing other modules may import
- `router.py`   HTTP endpoints (mounted under /v1 by `gharbasket.main`)
- `service.py`  business logic
- `schemas.py`  Pydantic request/response models
- `models.py`   SQLAlchemy tables, all in the module's own schema
- `events.py`   outbox events the module publishes
- `jobs.py`     its scheduled jobs and outbox consumers, the only place they register
                (`registry.load_modules()` imports every one)
"""

# Order follows the M-numbers in docs/modules/.
MODULES = (
    "identity",
    "catalog",
    "subscriptions",
    "orders",
    "billing",
    "hub_ops",
    "logistics",
    "partners",
    "community",
    "intel_gateway",
    "notifications",
    "analytics",
    "assistant",
)
