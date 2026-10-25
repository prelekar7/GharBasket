"""Outbox events published by M6 Hub Operations, named `hub_ops.<event>` (spec §5).

Nobody consumes the hub events yet (ADR-0026 §5); they exist so modules that keep a hub id can
follow hubs later without reading `hub_ops` tables.
"""

HUB_CREATED = "hub_ops.hub_created"  # hub_id, slug, name
HUB_UPDATED = "hub_ops.hub_updated"  # hub_id, changed: ["service_area", "status", ...]
# plan_id, hub_id, delivery_date, source, model, lines_count, order_paise, unpriced_lines, version:
# when a plan is built, and again when a rebuild changes it (§3.3)
PROCUREMENT_PLAN_READY = "hub_ops.procurement_plan_ready"
# purchase_order_id, number, plan_id, hub_id, vendor_id, delivery_date, lines_count, total_paise
PURCHASE_ORDER_SENT = "hub_ops.purchase_order_sent"
# receipt_id, hub_id, vendor_id, purchase_order_id, lines_count, rejected_lines, total_paise (§3.4)
GOODS_RECEIVED = "hub_ops.goods_received"
# waste_log_id, hub_id, lot_id, product_id, qty, unit, kg, reason, cascade_to (§3.5)
WASTE_LOGGED = "hub_ops.waste_logged"
