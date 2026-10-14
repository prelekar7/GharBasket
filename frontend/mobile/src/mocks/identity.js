// M1 household, address, member and consent mocks (docs/modules/M1-identity.md §3). Only the
// auth calls are frozen in §3.1; these request and response shapes are our proposal, built from
// the M1 tables, for the backend to confirm.
import { HttpResponse, http } from "msw";
import { caller, HOUSEHOLDS_KEY } from "./handlers.js";

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );

/** Created records, kept for the session so later screens (Profile) can read them. */
export const identityStore = {
  /** @type {Record<string, any>} */ households: {},
  /** @type {any[]} */ addresses: [],
  /** @type {any[]} */ members: [],
  /** @type {any[]} */ consents: [],
  /** @type {Record<string, any>} */ byIdempotencyKey: {},
};

const PURPOSES = ["account", "health_personalisation", "whatsapp_updates", "society_insights"];

export const identityHandlers = [
  http.post("*/v1/households", async ({ request }) => {
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(
        400,
        "platform.idempotency_key_required",
        "Idempotency-Key header is required",
      );
    // The same key returns the same household, so a retry never creates a second one.
    if (identityStore.byIdempotencyKey[key])
      return HttpResponse.json(identityStore.byIdempotencyKey[key], { status: 201 });
    const body = /** @type {any} */ (await request.json());
    if (!body.society_id || !(body.size > 0))
      return problem(422, "identity.household_invalid", "Society and household size are required");
    const household = {
      id: `hh-${Date.now()}`,
      version: 1,
      autopilot_enabled: true,
      auto_approve: false,
      ...body,
    };
    identityStore.households[household.id] = household;
    // From now on this number signs in as an existing household (…00005 stays new for demos).
    const phone = caller(request);
    const known = JSON.parse(localStorage.getItem(HOUSEHOLDS_KEY) ?? "[]");
    if (phone && phone !== "+919800000005" && !known.includes(phone))
      localStorage.setItem(HOUSEHOLDS_KEY, JSON.stringify([...known, phone]));
    identityStore.byIdempotencyKey[key] = household;
    return HttpResponse.json(household, { status: 201 });
  }),

  http.post("*/v1/addresses", async ({ request }) => {
    const body = /** @type {any} */ (await request.json());
    if (!identityStore.households[body.household_id])
      return problem(404, "identity.household_not_found", "Household not found");
    const address = { id: `addr-${identityStore.addresses.length + 1}`, ...body };
    identityStore.addresses.push(address);
    return HttpResponse.json(address, { status: 201 });
  }),

  http.post("*/v1/households/:id/members", async ({ request, params }) => {
    if (!identityStore.households[String(params.id)])
      return problem(404, "identity.household_not_found", "Household not found");
    const body = /** @type {any} */ (await request.json());
    const consented = identityStore.consents.some(
      (c) => c.purpose === "health_personalisation" && c.granted,
    );
    // Consent gating (M1 US-4): health tags are refused without a health consent on record.
    if (body.health_tags?.length && !consented) {
      return problem(403, "identity.health_consent_required", "Health tags need consent first");
    }
    const member = {
      id: `mem-${identityStore.members.length + 1}`,
      household_id: params.id,
      ...body,
    };
    identityStore.members.push(member);
    return HttpResponse.json(member, { status: 201 });
  }),

  http.post("*/v1/consents", async ({ request }) => {
    const body = /** @type {any} */ (await request.json());
    if (!PURPOSES.includes(body.purpose))
      return problem(422, "identity.consent_purpose_unknown", "Unknown consent purpose");
    const record = {
      id: `con-${identityStore.consents.length + 1}`,
      granted_at: new Date().toISOString(),
      ...body,
    };
    identityStore.consents = identityStore.consents
      .filter((c) => c.purpose !== body.purpose)
      .concat(record);
    return HttpResponse.json(record, { status: 201 });
  }),

  http.get("*/v1/consents", () => HttpResponse.json({ items: identityStore.consents })),
];
