"""Outbox events published by M2 Catalog & Pricing, named `catalog.<event>` (spec §5)."""

PRICES_PUBLISHED = "catalog.prices_published"
# { product_id, changed: [fields], active } on every create or update (§3.3).
PRODUCT_CHANGED = "catalog.product_changed"
# { recipe_id, changed: [fields], active }; M10's planner catalog cache drops the recipe.
RECIPE_CHANGED = "catalog.recipe_changed"
