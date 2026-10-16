"""Outbox events published by M2 Catalog & Pricing, named `catalog.<event>` (spec §5)."""

PRICES_PUBLISHED = "catalog.prices_published"
# { recipe_id, changed: [fields], active }; M10's planner catalog cache drops the recipe.
RECIPE_CHANGED = "catalog.recipe_changed"
