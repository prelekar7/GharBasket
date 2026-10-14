// Impact and NGO hand-off mocks (M9 `POST /v1/ngo-handoffs`, `GET /v1/impact`; M12 `GET
// /v1/analytics/impact?scope=`). The list, batch and report shapes are our proposal. Seeded story: October
// 2026, Kharghar hub; Sunrise Towers' numbers match what its families see on the mobile Society screen.
import { HttpResponse, http } from "msw";
import { idempotent, mockStore, problem, roleOf } from "./util.js";

/**
 * @typedef {object} Handoff `community.ngo_handoffs`
 * @property {string} id
 * @property {string} on ISO date
 * @property {string} items what was handed over, in words
 * @property {string} note where it came from ("From the 29 Sep pickup")
 * @property {string} given_to
 * @property {number} people_reached
 * @property {"photo" | "photo_due"} proof photos are stored privately; "photo_due" until one is added
 *
 * @typedef {object} Batch Donations the hub passed to the NGO that are not handed on yet.
 * @property {string} id
 * @property {string} received_on ISO date
 * @property {string} items
 * @property {string} note
 *
 * @typedef {object} Impact GET /v1/analytics/impact?scope=hub&hub_id=&month=
 * @property {string} month "2026-10"
 * @property {string} label "October 2026"
 * @property {string} refreshed_at the counters are materialised hourly
 * @property {{ food_saved_kg: number, items_donated: number, earned_paise: number, people_reached: number }} totals
 * @property {{ labels: string[], food_saved_kg: number[], items_donated: number[] }} trend
 * @property {{ id: string, name: string, food_saved_kg: number, items_donated: number, earned_paise: number }[]} societies
 */

/** @type {Handoff[]} */
const seedHandoffs = [
  {
    id: "ho-1",
    on: "2026-10-01",
    items: "School bag, 3 kg clothes",
    note: "From the 29 Sep pickup",
    given_to: "Class 5, municipal school, Sector 12",
    people_reached: 24,
    proof: "photo",
  },
  {
    id: "ho-2",
    on: "2026-10-06",
    items: "Bhindi 1.5 kg",
    note: "Surplus cascade",
    given_to: "Community kitchen, Sector 8",
    people_reached: 40,
    proof: "photo_due",
  },
];

/** @type {Batch[]} */
const seedBatches = [
  { id: "b1", received_on: "2026-10-06", items: "3 toys, 18 books", note: "From the 6 Oct pickup" },
];

const state = () =>
  mockStore("impact", () => ({
    handoffs: /** @type {Handoff[]} */ (structuredClone(seedHandoffs)),
    batches: /** @type {Batch[]} */ (structuredClone(seedBatches)),
  }));

/** @param {Request} request */
const staffOnly = (request) => {
  const role = roleOf(request);
  return role === "admin" || role === "hub_manager"
    ? null
    : problem(403, "auth.forbidden", "Only admins and hub managers can use this");
};

const MONTH_LABEL = "October 2026";

export const impactHandlers = [
  http.get("*/v1/analytics/impact", ({ request }) => {
    const denied = staffOnly(request);
    if (denied) return denied;
    const people = state().handoffs.reduce((n, h) => n + h.people_reached, 0);
    /** @type {Impact} */
    const impact = {
      month: "2026-10",
      label: MONTH_LABEL,
      refreshed_at: "2026-10-06T11:00:00+05:30",
      totals: {
        food_saved_kg: 53,
        items_donated: 30,
        earned_paise: 1_580_000,
        people_reached: people,
      },
      trend: {
        labels: ["Jul", "Aug", "Sep", "Oct"],
        food_saved_kg: [18, 29, 41, 53],
        items_donated: [6, 11, 19, 30],
      },
      societies: [
        {
          id: "sunrise-towers",
          name: "Sunrise Towers",
          food_saved_kg: 42,
          items_donated: 18,
          earned_paise: 1_240_000,
        },
        {
          id: "krishna-kunj",
          name: "Krishna Kunj",
          food_saved_kg: 11,
          items_donated: 12,
          earned_paise: 340_000,
        },
      ],
    };
    return HttpResponse.json(impact);
  }),

  http.get("*/v1/ngo-handoffs", ({ request }) => {
    const denied = staffOnly(request);
    if (denied) return denied;
    const { handoffs, batches } = state();
    return HttpResponse.json({
      month: "2026-10",
      label: MONTH_LABEL,
      items: [...handoffs].sort((a, b) => a.on.localeCompare(b.on)),
      open_batches: batches,
      report: {
        handoffs: handoffs.length,
        people_reached: handoffs.reduce((n, h) => n + h.people_reached, 0),
      },
      next_cursor: null,
    });
  }),

  // Record where a received batch went (US-6). The photo itself goes to private storage with its own
  // upload call once that exists; here only whether one was added.
  http.post("*/v1/ngo-handoffs", ({ request }) => {
    const denied = staffOnly(request);
    if (denied) return denied;
    return idempotent(request, (body) => {
      const s = state();
      const batch = s.batches.find((b) => b.id === body?.batch_id);
      const errors = [];
      if (!batch) errors.push({ field: "batch_id", detail: "Pick what you received" });
      if (!String(body?.given_to ?? "").trim())
        errors.push({ field: "given_to", detail: "Say who it went to" });
      const people = Number(body?.people_reached);
      if (!Number.isInteger(people) || people < 0)
        errors.push({ field: "people_reached", detail: "Enter a whole number" });
      if (errors.length || !batch)
        return problem(422, "request.invalid", "Check the form", { errors });
      /** @type {Handoff} */
      const handoff = {
        id: `ho-${s.handoffs.length + 1}`,
        on: "2026-10-06",
        items: batch.items[0].toUpperCase() + batch.items.slice(1),
        note: batch.note,
        given_to: String(body.given_to).trim(),
        people_reached: people,
        proof: body.has_photo ? "photo" : "photo_due",
      };
      s.handoffs.push(handoff);
      s.batches = s.batches.filter((b) => b.id !== batch.id);
      return HttpResponse.json(handoff, { status: 201 });
    });
  }),
];
