// M1 profile mocks (docs/modules/M1-identity.md §3): PATCH /v1/me, devices, data export and
// account deletion, plus GET /v1/households/me for the Profile summary (our proposal; M1 lists
// households but no read endpoint yet). Shapes beyond the frozen UserOut are our proposal.
import { HttpResponse, http } from "msw";
import { caller, PROFILE_KEY, userFor } from "./handlers.js";
import { identityStore } from "./identity.js";

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

  http.get("*/v1/households/me", ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    // The household made in onboarding this session, else the designs' family of four.
    const made = Object.values(identityStore.households).at(-1);
    const address = identityStore.addresses.at(-1);
    if (made)
      return HttpResponse.json({
        id: made.id,
        size: made.size,
        society_id: made.society_id,
        flat: address ? `${String(address.tower ?? "").toUpperCase()}-${address.flat}` : null,
      });
    if (phone === "+919800000005")
      return problem(404, "identity.household_not_found", "No household yet");
    return HttpResponse.json({ id: "hh-1", size: 4, society_id: "sunrise-towers", flat: "B-402" });
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
