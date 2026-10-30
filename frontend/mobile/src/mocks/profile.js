// M1 profile mocks (docs/modules/M1-identity.md §3): PATCH /v1/me, devices, data export and
// account deletion. The household, members, address and subscription mocks are in identity.js;
// Budget Lock (M3) is in subscriptions.js. Shapes beyond the frozen ones are our proposal.
import { HttpResponse, http } from "msw";
import { caller, PROFILE_KEY, rolesFor, userFor } from "./handlers.js";
import { demoId } from "./householdStore.js";
import { fieldProblem, problem, shapeError } from "./identity.js";

const LOCALES = ["en-IN", "hi-IN", "mr-IN"];
const DEVICES_KEY = "gb-mock-devices";
/** Mock-only: the token the server holds for the signed-in session (tests read it). */
export const PUSH_TOKEN_KEY = "gb-mock-push-token";

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

  // PUT /v1/me/push-token (PushTokenIn → 204): sets this session's FCM token, null clears it.
  http.put("*/v1/me/push-token", async ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    const body = await request.json().catch(() => undefined);
    if (body === undefined) return problem(400, "http.400", "The body cannot be read");
    const bad = shapeError(body, ["push_token"], ["push_token"]);
    if (bad) return fieldProblem(bad);
    const token = /** @type {any} */ (body).push_token;
    if (token !== null && (typeof token !== "string" || token.length < 1 || token.length > 4096))
      return fieldProblem({
        loc: ["push_token"],
        message: "String should have 1 to 4096 characters",
        type: "string_too_short",
      });
    // The same token on another session of this user is cleared there; the mock has one.
    if (token === null) localStorage.removeItem(PUSH_TOKEN_KEY);
    else localStorage.setItem(PUSH_TOKEN_KEY, token);
    return new HttpResponse(null, { status: 204 });
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
