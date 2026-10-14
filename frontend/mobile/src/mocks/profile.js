// M1 profile mocks (docs/modules/M1-identity.md §3): PATCH /v1/me, devices, data export and
// account deletion, plus this month's Budget Lock (M3, not shipped yet). The household, members,
// address and subscription mocks are in identity.js. Shapes beyond the frozen ones are our proposal.
import { HttpResponse, http } from "msw";
import { caller, isNewUser, PROFILE_KEY, rolesFor, userFor } from "./handlers.js";
import { demoId, saveHousehold } from "./householdStore.js";
import { accountOrDemo, fieldProblem, problem, shapeError } from "./identity.js";

const LOCALES = ["en-IN", "hi-IN", "mr-IN"];
const DEVICES_KEY = "gb-mock-devices";
const WHEN_OVER_KEY = "gb-mock-budget-when-over";
const WHEN_OVER = ["swap", "trim", "ask"];

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

/** @typedef {{ id: string, platform: "android" | "web", signed_in_at: string, last_seen_at: string, current: boolean }} Device */

/** @returns {Device[]} */
const devices = () =>
  JSON.parse(localStorage.getItem(DEVICES_KEY) ?? "null") ?? [
    {
      id: demoId(200),
      platform: "web",
      signed_in_at: new Date().toISOString(),
      last_seen_at: new Date().toISOString(),
      current: true,
    },
    {
      id: demoId(201),
      platform: "android",
      signed_in_at: "2026-09-20T18:30:00Z",
      last_seen_at: "2026-09-28T15:11:00Z",
      current: false,
    },
  ];

export const profileHandlers = [
  // PATCH /v1/me (MeUpdate): only the fields sent change; `name: null` clears it. → MeOut.
  http.patch("*/v1/me", async ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    const body = /** @type {Record<string, any>} */ (await request.json());
    const bad = shapeError(body, ["name", "locale", "senior_mode"], []);
    if (bad) return fieldProblem(bad);
    /** @type {Record<string, unknown>} */ const patch = {};
    if (body.name !== undefined) {
      const name = body.name === null ? null : String(body.name).trim();
      if (name !== null && (name.length < 1 || name.length > 80))
        return fieldProblem({
          loc: ["name"],
          message: "String should have 1 to 80 characters",
          type: "string_too_long",
        });
      patch.name = name;
    }
    if (typeof body.senior_mode === "boolean") patch.senior_mode = body.senior_mode;
    if (body.locale !== undefined && body.locale !== null) {
      if (!LOCALES.includes(String(body.locale)))
        return fieldProblem({
          loc: ["locale"],
          message: "Input should be 'en-IN', 'hi-IN' or 'mr-IN'",
          type: "enum",
        });
      patch.locale = body.locale;
    }
    const all = JSON.parse(localStorage.getItem(PROFILE_KEY) ?? "{}");
    all[phone] = { ...all[phone], ...patch };
    localStorage.setItem(PROFILE_KEY, JSON.stringify(all));
    return HttpResponse.json({ ...userFor(phone), roles: rolesFor(phone) });
  }),

  // GET /v1/budget-lock (M3): the month's limit, what is spent, and what Autopilot does when a
  // week would go over.
  http.get("*/v1/budget-lock", ({ request }) => {
    const phone = caller(request);
    const h = phone ? accountOrDemo(phone)?.household : null;
    if (!phone || !h) return problem(404, "subscriptions.not_found", "No subscription");
    return HttpResponse.json(budgetLock(phone, h));
  }),

  // PUT /v1/budget-lock (M3, Idempotency-Key): { budget_paise: number | null, when_over }. null
  // turns Budget Lock off. The household's budget changes too, so its version moves on.
  http.put("*/v1/budget-lock", async ({ request }) => {
    const phone = caller(request);
    const h = phone ? accountOrDemo(phone)?.household : null;
    if (!phone || !h) return problem(404, "subscriptions.not_found", "No subscription");
    if (!request.headers.get("idempotency-key"))
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
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

  // GET /v1/me/devices: a bare list, newest first.
  http.get("*/v1/me/devices", () => HttpResponse.json(devices())),

  // Signing out a device also ends its refresh token (M1).
  http.delete("*/v1/me/devices/:id", ({ params }) => {
    const list = devices();
    if (!list.some((d) => d.id === params.id))
      return problem(404, "identity.device_not_found", "No such signed-in device.");
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
