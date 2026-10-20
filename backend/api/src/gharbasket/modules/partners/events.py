"""Outbox events published by M8 Partner Network, named `partners.<event>` (spec §5).

No event carries KYC data, a number, an address or a phone.
"""

# {partner_id, user_id, kind, hub_id, requested_society_id}: requested_society_id is set for
# captains only. M1 grants the hub role of kitchens, riders and packers; M9 appoints a captain
# (ADR-0026 §4).
VERIFIED = "partners.verified"
# {partner_id, user_id, kind, hub_id, reason_code}; reason_code "left" on account deletion.
SUSPENDED = "partners.suspended"
# {partner_id, user_id, kind, hub_id}: M1 marks the hub role verified again; a captain is not
# re-appointed.
REINSTATED = "partners.reinstated"
# {application_id, user_id, kind, hub_id, scope_type, scope_id} (§3.1).
APPLICATION_SUBMITTED = "partners.application_submitted"
# {application_id, user_id, kind, hub_id, outcome}: outcome approved, resubmit or rejected; the
# reason is not in the event (M11 reads it through the facade).
APPLICATION_DECIDED = "partners.application_decided"
# {assignment_id, partner_id, user_id, hub_id, date, meal, portions, orders_count, respond_by,
# round} (§3.4): M11 pushes the offer to the kitchen.
ASSIGNMENT_CREATED = "partners.assignment_created"
# {hub_id, date, meal, run_id, order_ids, reasons}: once per run; M11 tells the hub managers.
TIFFINS_UNASSIGNED = "partners.tiffins_unassigned"
