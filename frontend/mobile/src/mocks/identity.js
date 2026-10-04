// M1 household, member and consent mocks, matching the frozen contract (docs/modules/M1-identity.md
// §3, contract #56; backend modules/identity/schemas.py). Onboarding is one atomic
// POST /v1/households with members, address and consents inside.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ detail,
  /** @type {Record<string, unknown>} */ extra = {},
) =>
  HttpResponse.json(
    { type: `urn:gharbasket:problem:${code}`, title: detail, status, code, detail, ...extra },
    { status, headers: { "content-type": "application/problem+json" } },
  );

/** Created records, kept for the session so later screens (Profile) can read them. */
export const identityStore = {
  /** @type {Record<string, any>} household id → HouseholdOut plus its owner's phone */ households:
    {},
  /** @type {Record<string, any>} */ byIdempotencyKey: {},
  /** @type {Record<string, string>} phone → society id of the household it created */ societyByPhone:
    {},
  /** @type {Record<string, Record<string, { version: string, granted_at: string }>>} phone → granted consents */
  consentsByPhone: {},
};

const SUNRISE_TOWERS = "0190f0aa-5000-7000-8000-000000000001";
/**
 * The society of the caller's household, or null. Demo sign-ins already have a household in
 * Sunrise Towers; only +91 98000 00005 starts as a brand-new household.
 * @param {string} phone
 */
export const mockHouseholdSocietyOf = (phone) =>
  identityStore.societyByPhone[phone] ?? (phone === "+919800000005" ? null : SUNRISE_TOWERS);

const PURPOSES = [
  "service_delivery",
  "personalisation",
  "health_personalisation",
  "marketing",
  "research_anonymised",
];
const REQUIRED = ["service_delivery", "personalisation"];
const CONSENT_VERSION = "v1";
const AGE_BANDS = ["under_5", "5_17", "18_59", "60_plus"];
const FLAT = /^[A-Z0-9 /-]{1,20}$/;

/** The caller's granted consents, by purpose. @param {string} phone */
const consentsOf = (phone) => {
  identityStore.consentsByPhone[phone] ??= {};
  return identityStore.consentsByPhone[phone];
};

/** HouseholdIn checks the backend makes, in the same order; null when the body is fine. @param {any} b */
function invalidHousehold(b) {
  const flat = String(b?.address?.flat ?? "")
    .trim()
    .toUpperCase();
  const ok =
    typeof b?.society_id === "string" &&
    ["veg", "egg", "nonveg"].includes(b.diet) &&
    typeof b.address?.tower_id === "string" &&
    FLAT.test(flat) &&
    Array.isArray(b.members) &&
    b.members.length >= 1 &&
    b.members.length <= 12 &&
    b.members.every((/** @type {any} */ m) => AGE_BANDS.includes(m.age_band)) &&
    Array.isArray(b.consents) &&
    b.consents.every(
      (/** @type {any} */ c) => PURPOSES.includes(c.purpose) && typeof c.granted === "boolean",
    ) &&
    (b.budget_paise === null || b.budget_paise === undefined || b.budget_paise >= 50_000);
  return ok ? null : problem(422, "request.invalid", "The request is not valid.");
}

export const identityHandlers = [
  http.post("*/v1/households", async ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Your session has expired.");
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Send an Idempotency-Key header.");
    // The same key replays the first answer, so a retry never creates a second household.
    if (identityStore.byIdempotencyKey[key])
      return HttpResponse.json(identityStore.byIdempotencyKey[key], { status: 201 });
    const body = /** @type {any} */ (await request.json());
    const existing = Object.values(identityStore.households).find((h) => h.owner === phone);
    if (existing)
      return problem(409, "identity.household_exists", "You already have a household.", {
        household_id: existing.id,
      });
    const invalid = invalidHousehold(body);
    if (invalid) return invalid;
    if (body.consents.some((/** @type {any} */ c) => c.version !== CONSENT_VERSION))
      return problem(409, "identity.consent_version_stale", "This privacy notice has changed.", {
        current_version: CONSENT_VERSION,
      });
    const granted = new Set(
      body.consents
        .filter((/** @type {any} */ c) => c.granted)
        .map((/** @type {any} */ c) => c.purpose),
    );
    if (!REQUIRED.every((p) => granted.has(p)))
      return problem(
        422,
        "identity.consent_required",
        "Delivery and personalisation consent are needed.",
      );
    if (
      !granted.has("health_personalisation") &&
      body.members.some((/** @type {any} */ m) => m.health_tags?.length)
    )
      return problem(403, "identity.health_consent_required", "Health tags need consent first.");
    const now = new Date().toISOString();
    const consents = consentsOf(phone);
    for (const c of body.consents)
      if (c.granted) consents[c.purpose] = { version: CONSENT_VERSION, granted_at: now };
    const id = crypto.randomUUID();
    const household = {
      id,
      version: 1,
      society_id: body.society_id,
      hub_id: null,
      size: body.members.length,
      diet: body.diet,
      cuisine_tags: body.cuisine_tags ?? [],
      weekday_cook_minutes: body.weekday_cook_minutes,
      weekend_cook_minutes: body.weekend_cook_minutes,
      no_cook_days: body.no_cook_days ?? [],
      budget_paise: body.budget_paise ?? null,
      autopilot_enabled: true,
      auto_approve: false,
      members: body.members.map((/** @type {any} */ m) => ({
        id: crypto.randomUUID(),
        sex: null,
        fasting_rules: { jain: false, fasts: [], no_onion_garlic_on_fast_days: false },
        health_tags: [],
        ...m,
      })),
      address: {
        id: crypto.randomUUID(),
        household_id: id,
        society_id: body.society_id,
        tower_id: body.address.tower_id,
        flat: String(body.address.flat).trim().toUpperCase(),
        location: body.address.location ?? null,
      },
      created_at: now,
    };
    identityStore.households[id] = { ...household, owner: phone };
    identityStore.byIdempotencyKey[key] = household;
    identityStore.societyByPhone[phone] = body.society_id;
    return HttpResponse.json(household, { status: 201, headers: { etag: '"1"' } });
  }),

  http.get("*/v1/households/me", ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Your session has expired.");
    const found = Object.values(identityStore.households).find((h) => h.owner === phone);
    if (!found) return problem(404, "identity.household_not_found", "No household found.");
    const { owner: _owner, ...household } = found;
    return HttpResponse.json(household, { headers: { etag: `"${household.version}"` } });
  }),

  // One entry per purpose: the latest grant, or "never given" with the current version.
  http.get("*/v1/consents", ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Your session has expired.");
    const mine = consentsOf(phone);
    return HttpResponse.json(
      PURPOSES.map((purpose) => ({
        purpose,
        version: mine[purpose]?.version ?? CONSENT_VERSION,
        granted: !!mine[purpose],
        granted_at: mine[purpose]?.granted_at ?? null,
        revoked_at: null,
      })),
    );
  }),
];
