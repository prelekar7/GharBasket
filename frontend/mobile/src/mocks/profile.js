// M1 profile mocks (docs/modules/M1-identity.md §3): PATCH /v1/me, devices, data export and
// account deletion, plus GET /v1/households/me for the Profile summary (our proposal; M1 lists
// households but no read endpoint yet). Shapes beyond the frozen UserOut are our proposal.
import { HttpResponse, http } from "msw";
import { caller, isNewUser, PROFILE_KEY, userFor } from "./handlers.js";
import { demoHousehold, householdOf, saveHousehold } from "./householdStore.js";

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );
const LOCALES = ["en-IN", "hi-IN", "mr-IN"];
const DEVICES_KEY = "gb-mock-devices";
const WHEN_OVER_KEY = "gb-mock-budget-when-over";
const WHEN_OVER = ["swap", "trim", "ask"];

/** The caller's household; a demo phone gets the designs' family the first time. @param {string} phone */
function householdOrDemo(phone) {
  let h = householdOf(phone);
  if (!h && !isNewUser(phone)) {
    h = demoHousehold();
    saveHousehold(phone, h);
  }
  return h;
}

/**
 * This month's Budget Lock (M3 budget_locks). The limit is the household's `budget_paise`, the
 * field onboarding writes; `when_over` and the counters are our proposal. A demo household has
 * spent the designs' ₹1,180 of October so far. @param {string} phone @param {import("./householdStore.js").Household} h
 */
const budgetLock = (phone, h) => ({
  month: "2026-10",
  budget_paise: h.budget_paise,
  spent_paise: isNewUser(phone) ? 0 : 118000,
  delivery_days_left: 10,
  when_over: JSON.parse(localStorage.getItem(WHEN_OVER_KEY) ?? "{}")[phone] ?? "swap",
  // A new limit applies from next week's plan; this week's box is already planned.
  applies_from: "2026-10-12",
});

/** @returns {{ id: string, platform: "android" | "web", label: string, last_seen_at: string, current: boolean }[]} */
const devices = () =>
  JSON.parse(localStorage.getItem(DEVICES_KEY) ?? "null") ?? [
    {
      id: "dev-this",
      platform: "web",
      label: "This phone",
      last_seen_at: new Date().toISOString(),
      current: true,
    },
    {
      id: "dev-2",
      platform: "android",
      label: "Redmi Note 13",
      last_seen_at: "2026-09-28T20:41:00+05:30",
      current: false,
    },
  ];

export const profileHandlers = [
  http.patch("*/v1/me", async ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    const body = /** @type {Record<string, unknown>} */ (await request.json());
    /** @type {Record<string, unknown>} */ const patch = {};
    if (typeof body.name === "string") patch.name = body.name.trim().slice(0, 60) || null;
    if (typeof body.senior_mode === "boolean") patch.senior_mode = body.senior_mode;
    if (body.locale !== undefined) {
      if (!LOCALES.includes(String(body.locale)))
        return problem(422, "identity.locale_unsupported", "Unsupported language");
      patch.locale = body.locale;
    }
    const all = JSON.parse(localStorage.getItem(PROFILE_KEY) ?? "{}");
    all[phone] = { ...all[phone], ...patch };
    localStorage.setItem(PROFILE_KEY, JSON.stringify(all));
    return HttpResponse.json(userFor(phone));
  }),

  // GET /v1/households/me (our proposal; M1 has GET /v1/households/{id}): the caller's household
  // with members, food rules, health (only with consent) and the subscription's pause.
  http.get("*/v1/households/me", ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    const h = householdOrDemo(phone);
    if (!h) return problem(404, "identity.household_not_found", "No household yet");
    return HttpResponse.json(h.health_consent ? h : { ...h, health: {} });
  }),

  // PATCH /v1/households/{id} with If-Match (M1). Member counts and health tags ride along here as a
  // proposal shortcut for the Profile editor; the spec has them under …/members.
  http.patch("*/v1/households/:id", async ({ request }) => {
    const phone = caller(request);
    const h = phone && householdOf(phone);
    if (!phone || !h) return problem(404, "identity.household_not_found", "No household yet");
    if (Number(request.headers.get("if-match")) !== h.version)
      return problem(412, "identity.version_conflict", "Your household changed; reload it");
    const body = /** @type {Record<string, any>} */ (await request.json());
    if (
      body.health &&
      Object.keys(body.health).length &&
      !(body.health_consent ?? h.health_consent)
    )
      return problem(403, "identity.health_consent_required", "Health tags need consent first");
    const next = { ...h, ...body, id: h.id, version: h.version + 1 };
    next.size = Object.values(next.members).reduce((a, b) => a + Number(b), 0);
    if (next.size < 1) return problem(422, "identity.household_invalid", "At least one person");
    if (!next.health_consent) next.health = {};
    saveHousehold(phone, next);
    return HttpResponse.json(next);
  }),

  // PATCH /v1/addresses/{id} (M1): a new wing or flat in the same society. Moving society means
  // a new hub and captain, so it goes through support instead.
  http.patch("*/v1/addresses/:id", async ({ request, params }) => {
    const phone = caller(request);
    const h = phone && householdOf(phone);
    if (!phone || !h || h.address_id !== params.id)
      return problem(404, "identity.address_not_found", "No such address");
    const { tower, flat } = /** @type {{ tower?: string, flat?: string }} */ (await request.json());
    if (!tower || !flat?.trim() || !/^[A-Za-z0-9-]{1,8}$/.test(flat.trim()))
      return problem(422, "identity.address_invalid", "Pick a wing and enter the flat number");
    const next = { ...h, flat: `${tower.toUpperCase()}-${flat.trim()}` };
    saveHousehold(phone, next);
    return HttpResponse.json({ id: params.id, tower, flat: flat.trim(), label: next.flat });
  }),

  // PATCH /v1/subscriptions/{id} (M3): { paused_until: "YYYY-MM-DD" | null } pauses or resumes.
  http.patch("*/v1/subscriptions/:id", async ({ request }) => {
    const phone = caller(request);
    const h = phone && householdOf(phone);
    if (!phone || !h) return problem(404, "subscriptions.not_found", "No subscription");
    const { paused_until: until = null } = /** @type {{ paused_until?: string | null }} */ (
      await request.json()
    );
    if (until !== null && !/^\d{4}-\d{2}-\d{2}$/.test(until))
      return problem(422, "subscriptions.pause_invalid", "paused_until must be a date");
    const subscription = {
      ...h.subscription,
      status: until ? "paused" : "active",
      paused_until: until,
    };
    saveHousehold(phone, { ...h, subscription });
    return HttpResponse.json(subscription);
  }),

  // GET /v1/budget-lock (M3): the month's limit, what is spent, and what Autopilot does when a
  // week would go over.
  http.get("*/v1/budget-lock", ({ request }) => {
    const phone = caller(request);
    const h = phone && householdOrDemo(phone);
    if (!phone || !h) return problem(404, "subscriptions.not_found", "No subscription");
    return HttpResponse.json(budgetLock(phone, h));
  }),

  // PUT /v1/budget-lock (M3, Idempotency-Key): { budget_paise: number | null, when_over }. null
  // turns Budget Lock off. The household's budget changes too, so its version moves on.
  http.put("*/v1/budget-lock", async ({ request }) => {
    const phone = caller(request);
    const h = phone && householdOrDemo(phone);
    if (!phone || !h) return problem(404, "subscriptions.not_found", "No subscription");
    if (!request.headers.get("idempotency-key"))
      return problem(
        400,
        "platform.idempotency_key_required",
        "Idempotency-Key header is required",
      );
    const { budget_paise: budget = null, when_over: mode = "swap" } =
      /** @type {{ budget_paise?: number | null, when_over?: string }} */ (await request.json());
    if (budget !== null && (!Number.isInteger(budget) || budget < 50000 || budget > 10_000_000))
      return problem(422, "subscriptions.budget_invalid", "Set a limit between ₹500 and ₹1,00,000");
    if (!WHEN_OVER.includes(mode))
      return problem(
        422,
        "subscriptions.budget_invalid",
        "Pick what happens when a week would go over",
      );
    const next = { ...h, budget_paise: budget, version: h.version + 1 };
    saveHousehold(phone, next);
    const all = JSON.parse(localStorage.getItem(WHEN_OVER_KEY) ?? "{}");
    localStorage.setItem(WHEN_OVER_KEY, JSON.stringify({ ...all, [phone]: mode }));
    return HttpResponse.json(budgetLock(phone, next));
  }),

  http.get("*/v1/me/devices", () => HttpResponse.json({ items: devices() })),

  // Revoking a device also ends its refresh token (M1).
  http.delete("*/v1/me/devices/:id", ({ params }) => {
    const list = devices();
    const device = list.find((d) => d.id === params.id);
    if (!device) return problem(404, "identity.device_not_found", "No such device");
    if (device.current)
      return problem(409, "identity.device_current", "Use Sign out for this phone");
    localStorage.setItem(DEVICES_KEY, JSON.stringify(list.filter((d) => d.id !== params.id)));
    return new HttpResponse(null, { status: 204 });
  }),

  // Export is asynchronous: the link arrives as a notification when the file is ready (M1).
  http.get("*/v1/me/data-export", () =>
    HttpResponse.json({ status: "queued", ready_within_hours: 24 }, { status: 202 }),
  ),

  // Deletion has a grace period before the purge job removes the account (M1, DPDP).
  http.delete("*/v1/me", ({ request }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    return HttpResponse.json({ status: "scheduled", purge_on: "2026-11-02" }, { status: 202 });
  }),
];
