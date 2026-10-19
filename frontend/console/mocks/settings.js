// Hub settings mocks (M6 hubs:write is admin-only). No spec lists these endpoints yet, so both are
// proposals: GET /v1/hubs/{id}/settings and PATCH /v1/hubs/{id}/settings (a partial update; every change
// is logged with who and when). The clock lists the scheduled jobs of a delivery day (T) as the other
// modules own them; this page shows it and does not edit it.
import { HttpResponse, http } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { mockStore, problem, roleOf } from "./util.js";

/**
 * @typedef {{ ai_planner: boolean, auto_approve: boolean, tiffins: boolean, upi_autopay: boolean }} Switches
 * @typedef {"en" | "hi" | "mr"} Language
 * @typedef {"22-07" | "21-08" | "23-06" | "off"} QuietHours
 * @typedef {{ switches: Switches, languages: Language[], quiet_hours: QuietHours }} Editable
 */

const CLOCK = [
  ["draft_weeks", "Draft weeks generated", "T-3 · 6:00 PM", "M3"],
  ["approve_reminder", "Reminder to approve", "T-1 · 7:00 PM", "M11"],
  ["cutoff", "Cut-off: orders lock", "T-1 · 9:00 PM", "M4"],
  ["topup_grace", "Top-up grace ends", "T-1 · 11:00 PM", "M5"],
  ["procurement", "Procurement plan", "T-1 · 9:15 PM", "M6"],
  ["tiffins_assigned", "Kitchen tiffins assigned", "T-1 · 9:30 PM", "M8"],
  ["packing", "Packing tasks", "T · 5:00 AM", "M6"],
  ["routes", "Routes published", "T · 7:00 AM", "M7"],
  ["cascade", "Surplus cascade", "T · 12:30 PM", "M6"],
  ["payouts", "Partner payouts", "Mondays · 10:00 AM", "M5"],
  ["retrain", "Models retrained", "Sundays · 3:00 AM", "M10"],
].map(([key, what, when, module]) => ({ key, what, when, module }));

const store = () =>
  mockStore("hub-settings", () => ({
    /** @type {Editable} */
    value: {
      switches: { ai_planner: true, auto_approve: true, tiffins: true, upi_autopay: false },
      languages: /** @type {Language[]} */ (["en", "hi", "mr"]),
      quiet_hours: /** @type {QuietHours} */ ("22-07"),
    },
    /** @type {{ at: string, by: string, change: string }[]} newest first */
    changes: [
      { at: "2026-10-01T09:40:00+05:30", by: "Neha Iyer", change: "Auto-approve switched on" },
    ],
  }));

const LABELS = /** @type {Record<string, string>} */ ({
  ai_planner: "AI meal planner",
  auto_approve: "Auto-approve for families who chose it",
  tiffins: "Tiffins",
  upi_autopay: "UPI Autopay",
});
const QUIET = /** @type {Record<string, string>} */ ({
  "22-07": "10 PM to 7 AM",
  "21-08": "9 PM to 8 AM",
  "23-06": "11 PM to 6 AM",
  off: "off",
});

const view = () => ({
  hub_id: KHARGHAR_HUB_ID,
  hub_name: "Kharghar hub",
  clock: CLOCK,
  ...store().value,
  changes: store().changes.slice(0, 5),
});

export const settingsHandlers = [
  http.get("*/v1/hubs/:hub/settings", ({ request }) =>
    roleOf(request) === "admin"
      ? HttpResponse.json(view())
      : problem(403, "hubs.forbidden", "Hub settings are for admins"),
  ),

  http.patch("*/v1/hubs/:hub/settings", async ({ request }) => {
    if (roleOf(request) !== "admin") {
      return problem(403, "hubs.forbidden", "Hub settings are for admins");
    }
    const patch = /** @type {Partial<Editable> | null} */ (await request.json());
    const s = store();
    /** @type {string[]} */
    const notes = [];
    for (const [key, on] of Object.entries(patch?.switches ?? {})) {
      if (!(key in s.value.switches) || typeof on !== "boolean") {
        return problem(422, "request.invalid", "Unknown switch");
      }
      const k = /** @type {keyof Switches} */ (key);
      if (s.value.switches[k] !== on) notes.push(`${LABELS[k]} switched ${on ? "on" : "off"}`);
      s.value.switches[k] = on;
    }
    if (patch?.languages) {
      const langs = patch.languages.filter((l) => ["en", "hi", "mr"].includes(l));
      if (langs.length === 0) {
        return problem(422, "hubs.language_required", "Keep at least one language on");
      }
      s.value.languages = langs;
      notes.push(`Languages: ${langs.join(", ")}`);
    }
    if (patch?.quiet_hours) {
      if (!(patch.quiet_hours in QUIET))
        return problem(422, "request.invalid", "Unknown quiet hours");
      s.value.quiet_hours = patch.quiet_hours;
      notes.push(`Quiet hours set to ${QUIET[patch.quiet_hours]}`);
    }
    if (notes.length) {
      s.changes.unshift({
        at: new Date().toISOString(),
        by: "Neha Iyer",
        change: notes.join("; "),
      });
    }
    return HttpResponse.json(view());
  }),
];
