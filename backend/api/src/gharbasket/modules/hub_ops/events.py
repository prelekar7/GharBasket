"""Outbox events published by M6 Hub Operations, named `hub_ops.<event>` (spec §5).

Nobody consumes the hub events yet (ADR-0026 §5); they exist so modules that keep a hub id can
follow hubs later without reading `hub_ops` tables.
"""

HUB_CREATED = "hub_ops.hub_created"  # hub_id, slug, name
HUB_UPDATED = "hub_ops.hub_updated"  # hub_id, changed: ["service_area", "status", ...]
