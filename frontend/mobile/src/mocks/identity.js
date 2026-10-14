// M1 household, address, member and consent mocks (docs/modules/M1-identity.md §3). Only the
// auth calls are frozen in §3.1; these request and response shapes are our proposal, built from
// the M1 tables, for the backend to confirm.
import { HttpResponse, http } from "msw";
import { caller, HOUSEHOLDS_KEY } from "./handlers.js";
import { householdOf, saveHousehold } from "./householdStore.js";

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

/** M1 age bands → the household's member groups. */
const GROUP_OF = /** @type {Record<string, "adults" | "seniors" | "children" | "little">} */ ({
  "18_59": "adults",
  "60_plus": "seniors",
  "5_17": "children",
  under_5: "little",
});

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
    if (phone)
      saveHousehold(phone, {
        id: household.id,
        version: 1,
        society_id: body.society_id,
        flat: null,
        address_id: null,
        size: body.size,
        // Members arrive one by one (POST …/members) and fill these counts.
        members: { adults: 0, seniors: 0, children: 0, little: 0 },
        diet: body.diet ?? "veg",
        cuisine_tags: body.cuisine_tags ?? [],
        rules: body.rules ?? { jain: false, no_onion_some_days: false },
        fasting: body.fasting ?? [],
        health_consent: false,
        health: {},
        weekday_cook_minutes: body.weekday_cook_minutes ?? 30,
        weekend_cook_minutes: body.weekend_cook_minutes ?? 60,
        no_cook_days: body.no_cook_days ?? [],
        budget_paise: body.budget_paise ?? null,
        subscription: { id: `sub-${household.id}`, status: "active", paused_until: null },
      });
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
    const phone = caller(request);
    const saved = phone && householdOf(phone);
    if (saved)
      saveHousehold(phone, {
        ...saved,
        flat: `${String(body.tower ?? "").toUpperCase()}-${body.flat}`,
      });
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
    const phone = caller(request);
    const saved = phone && householdOf(phone);
    const group = GROUP_OF[body.age_band];
    if (saved && group) {
      const n = saved.members[group] + 1;
      const tags = body.health_tags ?? [];
      saveHousehold(phone, {
        ...saved,
        members: { ...saved.members, [group]: n },
        health: tags.length ? { ...saved.health, [`${group}-${n}`]: tags } : saved.health,
        health_consent: saved.health_consent || consented,
      });
    }
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
