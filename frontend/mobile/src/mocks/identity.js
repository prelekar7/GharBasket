// M1 household and consent mocks (docs/modules/M1-identity.md §3.2), the real shapes: onboarding
// is ONE call (POST /v1/households) with the members, the address and the consents inside it.
import { HttpResponse, http } from "msw";
import { caller, HOUSEHOLDS_KEY, isNewUser } from "./handlers.js";
import {
  accountOf,
  CONSENT_VERSION,
  consentsFor,
  demoAccount,
  PURPOSES,
  saveAccount,
  saveHousehold,
} from "./householdStore.js";

/**
 * A problem+json answer. `extra` holds extension members (household_id, current_version) and
 * `errors` the field errors of a 422 request.invalid, as the real API sends them.
 * @param {number} status @param {string} code @param {string} title
 * @param {Record<string, unknown>} [extra]
 */
export const problem = (status, code, title, extra = {}) =>
  HttpResponse.json(
    {
      type: `https://gharbasket.in/problems/${code}`,
      title,
      status,
      detail: title,
      code,
      ...extra,
    },
    { status, headers: { "content-type": "application/problem+json" } },
  );

/** @typedef {{ loc: (string | number)[], message: string, type: string }} FieldProblem */

/** 422 request.invalid for one field error, like FastAPI's (`loc` starts with "body"). @param {FieldProblem} e */
export const fieldProblem = (e) =>
  problem(422, "request.invalid", "The request is not valid", {
    errors: [{ ...e, loc: ["body", ...e.loc] }],
  });

/**
 * The first thing wrong with an object body (every request schema is additionalProperties:
 * false), or null.
 * @param {any} body @param {string[]} allowed @param {string[]} required @param {(string | number)[]} [at]
 * @returns {FieldProblem | null}
 */
export function shapeError(body, allowed, required, at = []) {
  if (!body || typeof body !== "object" || Array.isArray(body))
    return { loc: at, message: "Input should be an object", type: "model_attributes_type" };
  const extra = Object.keys(body).find((k) => !allowed.includes(k));
  if (extra)
    return {
      loc: [...at, extra],
      message: "Extra inputs are not permitted",
      type: "extra_forbidden",
    };
  const missing = required.find((k) => body[k] === undefined);
  if (missing) return { loc: [...at, missing], message: "Field required", type: "missing" };
  return null;
}

const MEMBER_KEYS = ["age_band", "sex", "fasting_rules", "health_tags"];
const RULE_KEYS = ["jain", "fasts", "no_onion_garlic_on_fast_days"];
export const AGE_BANDS = ["under_5", "5_17", "18_59", "60_plus"];

/**
 * The first schema error in one MemberIn (or MemberUpdate) body, or null.
 * @param {any} m @param {(string | number)[]} at @param {boolean} [create]
 */
export function memberError(m, at, create = true) {
  const bad = shapeError(m, MEMBER_KEYS, create ? ["age_band"] : [], at);
  if (bad) return bad;
  if (m.age_band != null && !AGE_BANDS.includes(m.age_band))
    return { loc: [...at, "age_band"], message: "Input should be a valid age band", type: "enum" };
  if (m.fasting_rules) return shapeError(m.fasting_rules, RULE_KEYS, [], [...at, "fasting_rules"]);
  return null;
}

/** The caller's account; a demo phone gets the designs' family the first time. @param {string} phone */
export function accountOrDemo(phone) {
  let account = accountOf(phone);
  if (!account && !isNewUser(phone)) {
    account = demoAccount();
    saveAccount(phone, account);
  }
  return account;
}

/** The household's new ETag header, quoted as the spec writes it. @param {number} version */
const etag = (version) => ({ etag: `"${version}"` });

/** `If-Match` as a number, quoted or not. @param {Request} request */
const ifMatch = (request) => {
  const raw = request.headers.get("if-match");
  return raw === null ? null : Number(raw.replace(/^W\//, "").replaceAll('"', ""));
};

/** Created households by Idempotency-Key: a retry gets the same one, a changed body is refused. */
const KEYS = "gb-mock-household-keys";
/** @returns {Record<string, { fingerprint: string, body: any }>} */
const createdByKey = () => JSON.parse(localStorage.getItem(KEYS) ?? "{}");

const HOUSEHOLD_KEYS = [
  "society_id",
  "address",
  "diet",
  "cuisine_tags",
  "weekday_cook_minutes",
  "weekend_cook_minutes",
  "no_cook_days",
  "budget_paise",
  "members",
  "consents",
];
const HOUSEHOLD_REQUIRED = [
  "diet",
  "weekday_cook_minutes",
  "weekend_cook_minutes",
  "society_id",
  "address",
  "members",
  "consents",
];
const ADDRESS_KEYS = ["tower_id", "flat", "location"];
const CONSENT_KEYS = ["purpose", "version", "granted"];

/** The first schema error in a HouseholdIn body, or null. @param {any} b @returns {FieldProblem | null} */
function householdError(b) {
  const bad = shapeError(b, HOUSEHOLD_KEYS, HOUSEHOLD_REQUIRED);
  if (bad) return bad;
  const address = shapeError(b.address, ADDRESS_KEYS, ADDRESS_KEYS, ["address"]);
  if (address) return address;
  if (!Array.isArray(b.members) || b.members.length < 1 || b.members.length > 12)
    return { loc: ["members"], message: "Members must have 1 to 12 items", type: "too_short" };
  for (const [i, m] of b.members.entries()) {
    const e = memberError(m, ["members", i]);
    if (e) return e;
  }
  for (const [i, c] of b.consents.entries()) {
    const e = shapeError(c, CONSENT_KEYS, CONSENT_KEYS, ["consents", i]);
    if (e) return e;
    if (!PURPOSES.includes(c.purpose))
      return {
        loc: ["consents", i, "purpose"],
        message: "Input should be a valid purpose",
        type: "enum",
      };
  }
  return null;
}

const HOUSEHOLD_PATCH_KEYS = [
  "diet",
  "cuisine_tags",
  "weekday_cook_minutes",
  "weekend_cook_minutes",
  "no_cook_days",
  "budget_paise",
  "autopilot_enabled",
  "auto_approve",
];
const NO_RULES = { jain: false, fasts: [], no_onion_garlic_on_fast_days: false };

/** The household with these members: the size follows and the version moves on. @param {import("./householdStore.js").Household} h @param {import("./householdStore.js").Member[]} members */
const withMembers = (h, members) => ({
  ...h,
  members,
  size: members.length,
  version: h.version + 1,
});

/** A 403 or 422 when a member body breaks the consent or Jain rule, else null. @param {import("./householdStore.js").Account} account @param {any} body @param {string} diet */
function memberRefusal(account, body, diet) {
  if (
    body.health_tags?.length &&
    !account.consents.some((c) => c.purpose === "health_personalisation" && c.granted)
  )
    return problem(403, "identity.health_consent_required", "Health tags need consent first");
  if (body.fasting_rules?.jain && diet !== "veg")
    return problem(422, "identity.diet_conflict", "A Jain household eats veg");
  return null;
}

export const identityHandlers = [
  // Onboarding in one call (M1 §3.2): household, members, address and consents in one transaction.
  http.post("*/v1/households", async ({ request }) => {
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    const body = /** @type {any} */ (await request.json());
    const fingerprint = JSON.stringify(body);
    const seen = createdByKey()[key];
    if (seen && seen.fingerprint !== fingerprint)
      return problem(
        422,
        "request.idempotency_mismatch",
        "That key was used with a different request",
      );
    if (seen) return HttpResponse.json(seen.body, { status: 201, headers: etag(1) });
    const bad = householdError(body);
    if (bad) return fieldProblem(bad);
    const existing = accountOf(phone);
    if (existing)
      return problem(409, "identity.household_exists", "You already have a household", {
        household_id: existing.household.id,
      });
    if (body.members.some((/** @type {any} */ m) => m.fasting_rules?.jain) && body.diet !== "veg")
      return problem(422, "identity.diet_conflict", "A Jain household eats veg");
    const granted = body.consents
      .filter((/** @type {any} */ c) => c.granted)
      .map((/** @type {any} */ c) => c.purpose);
    if (!granted.includes("service_delivery") || !granted.includes("personalisation"))
      return problem(422, "identity.consent_required", "Delivery and personalisation are needed");
    if (
      body.members.some((/** @type {any} */ m) => m.health_tags?.length) &&
      !granted.includes("health_personalisation")
    )
      return problem(403, "identity.health_consent_required", "Health tags need consent first");

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
        age_band: m.age_band,
        sex: m.sex ?? null,
        fasting_rules: {
          jain: false,
          fasts: [],
          no_onion_garlic_on_fast_days: false,
          ...m.fasting_rules,
        },
        health_tags: m.health_tags ?? [],
      })),
      address: {
        id: crypto.randomUUID(),
        household_id: id,
        society_id: body.society_id,
        tower_id: body.address.tower_id,
        flat: String(body.address.flat).trim().toUpperCase(),
        location: body.address.location,
      },
      created_at: new Date().toISOString(),
    };
    saveAccount(phone, { household, subscription: null, consents: consentsFor(granted) });
    // From now on this number signs in as an existing household (…00005 stays new for demos).
    const known = JSON.parse(localStorage.getItem(HOUSEHOLDS_KEY) ?? "[]");
    if (phone !== "+919800000005" && !known.includes(phone))
      localStorage.setItem(HOUSEHOLDS_KEY, JSON.stringify([...known, phone]));
    localStorage.setItem(
      KEYS,
      JSON.stringify({ ...createdByKey(), [key]: { fingerprint, body: household } }),
    );
    return HttpResponse.json(household, { status: 201, headers: etag(1) });
  }),

  // GET /v1/consents: a bare list, one entry per purpose, including the never-given ones.
  http.get("*/v1/consents", ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    return HttpResponse.json(accountOf(phone)?.consents ?? consentsFor([]));
  }),

  // POST /v1/consents: grant or revoke one purpose → 200. Revoking health erases the members' tags.
  http.post("*/v1/consents", async ({ request }) => {
    const phone = caller(request);
    const account = phone ? accountOf(phone) : null;
    if (!phone || !account) return problem(401, "auth.invalid_token", "Sign in again");
    const body = /** @type {any} */ (await request.json());
    const bad = shapeError(body, CONSENT_KEYS, CONSENT_KEYS);
    if (bad) return fieldProblem(bad);
    if (!PURPOSES.includes(body.purpose))
      return fieldProblem({
        loc: ["purpose"],
        message: "Input should be a valid purpose",
        type: "enum",
      });
    if (body.version !== CONSENT_VERSION)
      return problem(409, "identity.consent_version_stale", "Show the new notice", {
        current_version: CONSENT_VERSION,
      });
    const now = new Date().toISOString();
    const record = {
      purpose: body.purpose,
      version: body.version,
      granted: body.granted,
      granted_at: body.granted ? now : null,
      revoked_at: body.granted ? null : now,
    };
    saveAccount(phone, {
      ...account,
      consents: account.consents.map((c) => (c.purpose === body.purpose ? record : c)),
    });
    if (body.purpose === "health_personalisation" && !body.granted)
      saveHousehold(phone, {
        ...account.household,
        version: account.household.version + 1,
        members: account.household.members.map((m) => ({ ...m, health_tags: [] })),
      });
    return HttpResponse.json(record);
  }),

  // GET /v1/households/me: HouseholdOut with its ETag, or 404 before onboarding.
  http.get("*/v1/households/me", ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    const account = accountOrDemo(phone);
    if (!account) return problem(404, "identity.household_not_found", "No household yet");
    return HttpResponse.json(account.household, { headers: etag(account.household.version) });
  }),

  // PATCH /v1/households/{id} (If-Match): only the household's own settings; the family goes
  // through …/members and the address through /v1/addresses.
  http.patch("*/v1/households/:id", async ({ request, params }) => {
    const phone = caller(request);
    const account = phone ? accountOf(phone) : null;
    if (!phone || !account || account.household.id !== params.id)
      return problem(404, "identity.household_not_found", "No such household");
    const h = account.household;
    const match = ifMatch(request);
    if (match === null)
      return problem(428, "request.precondition_required", "If-Match is required");
    const body = /** @type {any} */ (await request.json());
    const bad = shapeError(body, HOUSEHOLD_PATCH_KEYS, []);
    if (bad) return fieldProblem(bad);
    if (match !== h.version)
      return problem(412, "identity.version_conflict", "Your household changed; reload it");
    if (body.budget_paise != null && body.budget_paise < 50000)
      return fieldProblem({
        loc: ["budget_paise"],
        message: "Input should be greater than or equal to 50000",
        type: "greater_than_equal",
      });
    const next = { ...h, ...body, version: h.version + 1 };
    if (next.diet !== "veg" && h.members.some((m) => m.fasting_rules.jain))
      return problem(422, "identity.diet_conflict", "A Jain household eats veg");
    saveHousehold(phone, next);
    return HttpResponse.json(next, { headers: etag(next.version) });
  }),

  // POST /v1/households/{id}/members → 201 MemberOut; the household's version and size move on.
  http.post("*/v1/households/:id/members", async ({ request, params }) => {
    const phone = caller(request);
    const account = phone ? accountOf(phone) : null;
    if (!phone || !account || account.household.id !== params.id)
      return problem(404, "identity.household_not_found", "No such household");
    const body = /** @type {any} */ (await request.json());
    const bad = memberError(body, []);
    if (bad) return fieldProblem(bad);
    const h = account.household;
    if (h.members.length >= 12)
      return problem(422, "identity.household_full", "A household has up to 12 people");
    const refused = memberRefusal(account, body, h.diet);
    if (refused) return refused;
    const member = {
      id: crypto.randomUUID(),
      age_band: body.age_band,
      sex: body.sex ?? null,
      fasting_rules: { ...NO_RULES, ...body.fasting_rules },
      health_tags: body.health_tags ?? [],
    };
    const next = withMembers(h, [...h.members, member]);
    saveHousehold(phone, next);
    return HttpResponse.json(member, { status: 201, headers: etag(next.version) });
  }),

  // PATCH …/members/{id}: only the fields sent change; fasting_rules is replaced whole.
  http.patch("*/v1/households/:id/members/:mid", async ({ request, params }) => {
    const phone = caller(request);
    const account = phone ? accountOf(phone) : null;
    const h = account && account.household.id === params.id ? account.household : null;
    const old = h?.members.find((m) => m.id === params.mid);
    if (!phone || !account || !h || !old)
      return problem(404, "identity.member_not_found", "No such member");
    const body = /** @type {any} */ (await request.json());
    const bad = memberError(body, [], false);
    if (bad) return fieldProblem(bad);
    const refused = memberRefusal(account, body, h.diet);
    if (refused) return refused;
    const member = {
      ...old,
      ...body,
      ...(body.fasting_rules ? { fasting_rules: { ...NO_RULES, ...body.fasting_rules } } : {}),
    };
    const next = withMembers(
      h,
      h.members.map((m) => (m.id === old.id ? member : m)),
    );
    saveHousehold(phone, next);
    return HttpResponse.json(member, { headers: etag(next.version) });
  }),

  // DELETE …/members/{id} → 204; the last member stays.
  http.delete("*/v1/households/:id/members/:mid", ({ request, params }) => {
    const phone = caller(request);
    const account = phone ? accountOf(phone) : null;
    const h = account && account.household.id === params.id ? account.household : null;
    if (!phone || !h?.members.some((m) => m.id === params.mid))
      return problem(404, "identity.member_not_found", "No such member");
    if (h.members.length === 1)
      return problem(422, "identity.household_needs_member", "A household needs one person");
    const next = withMembers(
      h,
      h.members.filter((m) => m.id !== params.mid),
    );
    saveHousehold(phone, next);
    return new HttpResponse(null, { status: 204, headers: etag(next.version) });
  }),

  // PATCH /v1/addresses/{id}: a new wing or flat; `flat` is the whole label ("B-402").
  http.patch("*/v1/addresses/:id", async ({ request, params }) => {
    const phone = caller(request);
    const account = phone ? accountOf(phone) : null;
    if (!phone || !account || account.household.address.id !== params.id)
      return problem(404, "identity.address_not_found", "No such address");
    const body = /** @type {any} */ (await request.json());
    const bad = shapeError(body, ["tower_id", "flat", "society_id", "location"], []);
    if (bad) return fieldProblem(bad);
    const flat = body.flat === undefined ? undefined : String(body.flat).trim().toUpperCase();
    if (flat !== undefined && !/^[A-Z0-9 /-]{1,20}$/.test(flat))
      return fieldProblem({
        loc: ["flat"],
        message: "String should match pattern",
        type: "string_pattern_mismatch",
      });
    const h = account.household;
    const address = {
      ...h.address,
      ...(body.tower_id ? { tower_id: body.tower_id } : {}),
      ...(flat ? { flat } : {}),
      ...(body.location !== undefined ? { location: body.location } : {}),
    };
    saveHousehold(phone, { ...h, address, version: h.version + 1 });
    return HttpResponse.json(address);
  }),

  // POST /v1/subscriptions (Idempotency-Key): one per household; UPI Autopay is not open yet.
  http.post("*/v1/subscriptions", async ({ request }) => {
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const phone = caller(request);
    const account = phone ? accountOrDemo(phone) : null;
    if (!phone || !account) return problem(404, "identity.household_not_found", "No household yet");
    const body = /** @type {any} */ ((await request.json()) ?? {});
    const bad = shapeError(body, ["plan", "payment_mode"], []);
    if (bad) return fieldProblem(bad);
    const { plan = "autopilot", payment_mode: mode = "wallet" } = body;
    if (!["autopilot", "tiffin", "salad"].includes(plan))
      return problem(422, "subscriptions.plan_unknown", "No such plan.");
    if (!["wallet", "autopay"].includes(mode))
      return fieldProblem({
        loc: ["payment_mode"],
        message: "Input should be 'wallet' or 'autopay'",
        type: "enum",
      });
    if (mode !== "wallet")
      return problem(
        422,
        "subscriptions.payment_mode_unavailable",
        "UPI Autopay arrives with the pilot.",
      );
    if (account.subscription)
      return problem(409, "subscriptions.exists", "This household already has a subscription.");
    const subscription = {
      id: crypto.randomUUID(),
      household_id: account.household.id,
      plan,
      status: /** @type {const} */ ("active"),
      payment_mode: mode,
      paused_until: null,
      auto_approve: false,
      started_at: new Date().toISOString(),
    };
    saveAccount(phone, { ...account, subscription });
    return HttpResponse.json(subscription, { status: 201 });
  }),

  http.get("*/v1/subscriptions/current", ({ request }) => {
    const phone = caller(request);
    const account = phone ? accountOrDemo(phone) : null;
    if (!account?.subscription) return problem(404, "subscriptions.not_found", "Nothing here yet.");
    return HttpResponse.json(account.subscription);
  }),

  // PATCH /v1/subscriptions/current: { paused_until?: "YYYY-MM-DD" | null, auto_approve? }. A date
  // after today pauses; null (or a past date) resumes.
  http.patch("*/v1/subscriptions/current", async ({ request }) => {
    const phone = caller(request);
    const account = phone ? accountOrDemo(phone) : null;
    if (!phone || !account?.subscription)
      return problem(404, "subscriptions.not_found", "Nothing here yet.");
    const body = /** @type {any} */ (await request.json());
    const bad = shapeError(body, ["paused_until", "auto_approve"], []);
    if (bad) return fieldProblem(bad);
    const until = body.paused_until;
    if (until != null && !/^\d{4}-\d{2}-\d{2}$/.test(until))
      return fieldProblem({
        loc: ["paused_until"],
        message: "Input should be a valid date",
        type: "date_from_datetime_parsing",
      });
    let subscription = account.subscription;
    if (until !== undefined) {
      const paused = until !== null && until > new Date().toISOString().slice(0, 10);
      subscription = { ...subscription, paused_until: until, status: paused ? "paused" : "active" };
    }
    if (typeof body.auto_approve === "boolean")
      subscription = { ...subscription, auto_approve: body.auto_approve };
    saveAccount(phone, { ...account, subscription });
    return HttpResponse.json(subscription);
  }),
];
