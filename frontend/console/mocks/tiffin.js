// Tiffin menu approval mocks (M2 `catalog.tiffin_menus`, M8 kitchens). The spec names the table (id, hub_id, partner_id,
// date, meal, product_id, recipe_ids, price_paise, status draft | approved | withdrawn) and the `tiffin_menus:write`
// permission (kitchen proposes, hub manager approves) but no paths, so the paths here are our proposal (@proposal): the
// real API answers 404 and the page says "not available yet". A waiting menu is `draft`, an approved one `approved`, one
// sent back `withdrawn`. The price families pay is the catalog price; what the kitchen is paid is the hub's rate card
// (M8 §3.6, `GET /v1/hubs/{id}/partner-rates`, in partners.js). Neither is computed by the console or by an LLM.
import { HttpResponse, http } from "msw";
import { deny, istDay } from "./adminShared.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { PARTNER_ID } from "./partner-ids.js";
import { fieldError, idempotent, mockStore, mockUuid, problem } from "./util.js";

/**
 * @typedef {{ id: string, hub_id: string, partner_id: string, date: string, name: string, components: string,
 *   kitchen: { id: string, name: string }, meal: "lunch" | "dinner", tags: string[], price_paise: number,
 *   contains: string[], fasting_ok: boolean, fasting_note: string, daily_capacity: number,
 *   status: "draft" | "approved" | "withdrawn", is_new: boolean, proposed_at: string,
 *   decided_at: string | null, decided_by: string | null, note: string | null }} TiffinDish
 */

/** @type {() => TiffinDish[]} */
const seed = () => [
  {
    id: mockUuid("b500", 1),
    hub_id: KHARGHAR_HUB_ID,
    partner_id: PARTNER_ID.lata,
    date: istDay(1, 0, 0).slice(0, 10),
    name: "Puran poli thali",
    components: "Puran poli, katachi amti, rice, salad",
    kitchen: { id: PARTNER_ID.lata, name: "Lata Shinde" },
    meal: "lunch",
    tags: ["Vegetarian", "festival"],
    price_paise: 11000,
    contains: ["Wheat", "jaggery", "toor dal"],
    fasting_ok: false,
    fasting_note: "Not suitable (has grains)",
    daily_capacity: 20,
    status: "draft",
    is_new: true,
    proposed_at: istDay(-1, 17, 40),
    decided_at: null,
    decided_by: null,
    note: null,
  },
  {
    id: mockUuid("b500", 2),
    hub_id: KHARGHAR_HUB_ID,
    partner_id: PARTNER_ID.lata,
    date: istDay(1, 0, 0).slice(0, 10),
    name: "Sabudana khichdi (upvas)",
    components: "Sabudana, groundnuts, potato, curd",
    kitchen: { id: PARTNER_ID.lata, name: "Lata Shinde" },
    meal: "lunch",
    tags: ["Fasting", "no onion or garlic"],
    price_paise: 11000,
    contains: ["Sabudana", "groundnuts", "potato", "curd"],
    fasting_ok: true,
    fasting_note: "Suitable (no grains)",
    daily_capacity: 20,
    status: "draft",
    is_new: true,
    proposed_at: istDay(-1, 17, 52),
    decided_at: null,
    decided_by: null,
    note: null,
  },
  {
    id: mockUuid("b500", 3),
    hub_id: KHARGHAR_HUB_ID,
    partner_id: PARTNER_ID.lata,
    date: istDay(1, 0, 0).slice(0, 10),
    name: "Varan-bhaat thali",
    components: "Approved 28 Sep",
    kitchen: { id: PARTNER_ID.lata, name: "Lata Shinde" },
    meal: "lunch",
    tags: ["Vegetarian"],
    price_paise: 11000,
    contains: ["Rice", "toor dal", "ghee"],
    fasting_ok: false,
    fasting_note: "Not suitable (has grains)",
    daily_capacity: 20,
    status: "approved",
    is_new: false,
    proposed_at: istDay(-9, 11, 0),
    decided_at: istDay(-8, 10, 15),
    decided_by: "Vikram Desai",
    note: null,
  },
  {
    id: mockUuid("b500", 4),
    hub_id: KHARGHAR_HUB_ID,
    partner_id: PARTNER_ID.lata,
    date: istDay(1, 0, 0).slice(0, 10),
    name: "Rajma-chawal thali",
    components: "Rajma, jeera rice, kachumber, papad",
    kitchen: { id: PARTNER_ID.lata, name: "Lata Shinde" },
    meal: "lunch",
    tags: ["Vegetarian"],
    price_paise: 12000,
    contains: ["Rice", "rajma", "curd"],
    fasting_ok: false,
    fasting_note: "Not suitable (has grains)",
    daily_capacity: 20,
    status: "approved",
    is_new: false,
    proposed_at: istDay(-14, 12, 0),
    decided_at: istDay(-13, 9, 30),
    decided_by: "Vikram Desai",
    note: null,
  },
  {
    id: mockUuid("b500", 5),
    hub_id: KHARGHAR_HUB_ID,
    partner_id: PARTNER_ID.lata,
    date: istDay(1, 0, 0).slice(0, 10),
    name: "Pithla-bhakri thali",
    components: "Pithla, jowar bhakri, thecha, onion",
    kitchen: { id: PARTNER_ID.lata, name: "Lata Shinde" },
    meal: "dinner",
    tags: ["Vegetarian", "gluten-free"],
    price_paise: 10000,
    contains: ["Besan", "jowar"],
    fasting_ok: false,
    fasting_note: "Not suitable (has grains)",
    daily_capacity: 20,
    status: "approved",
    is_new: false,
    proposed_at: istDay(-20, 15, 0),
    decided_at: istDay(-19, 10, 5),
    decided_by: "Vikram Desai",
    note: null,
  },
];

const dishes = () => mockStore("tiffin.dishes", seed);
const STAFF = ["admin", "hub_manager"];
const WHO = { admin: "Neha Iyer", hub_manager: "Vikram Desai" };

/** @param {Request} request */
const actor = (request) =>
  WHO[/** @type {"admin"} */ (request.headers.get("x-gb-role") ?? "admin")];

export const tiffinHandlers = [
  http.get("*/v1/hubs/:hub/tiffin-menus", ({ request, params }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const status = new URL(request.url).searchParams.get("status");
    const items = dishes().filter(
      (d) => d.hub_id === params.hub && (!status || d.status === status),
    );
    return HttpResponse.json({ items, next_cursor: null });
  }),

  http.post(
    "*/v1/tiffin-menus/:id/approve",
    ({ request, params }) =>
      deny(request, STAFF) ??
      idempotent(request, () => {
        const d = dishes().find((x) => x.id === params.id);
        if (!d) return problem(404, "tiffin.not_found", "No such dish");
        if (d.status !== "draft") return problem(409, "tiffin.not_waiting", "Already handled");
        d.status = "approved";
        d.decided_at = new Date().toISOString();
        d.decided_by = actor(request);
        d.note = null;
        return HttpResponse.json(d);
      }),
  ),

  http.post(
    "*/v1/tiffin-menus/:id/send-back",
    ({ request, params }) =>
      deny(request, STAFF) ??
      idempotent(request, (body) => {
        const d = dishes().find((x) => x.id === params.id);
        if (!d) return problem(404, "tiffin.not_found", "No such dish");
        if (d.status !== "draft") return problem(409, "tiffin.not_waiting", "Already handled");
        const note = String(body?.note ?? "").trim();
        if (!note)
          return problem(422, "request.invalid", "Tell the kitchen what to change", {
            errors: [fieldError("note", "Tell the kitchen what to change", "string_too_short")],
          });
        d.status = "withdrawn";
        d.decided_at = new Date().toISOString();
        d.decided_by = actor(request);
        d.note = note;
        return HttpResponse.json(d);
      }),
  ),

  // Undo of approve or send back: the dish returns to the waiting list.
  http.post(
    "*/v1/tiffin-menus/:id/reopen",
    ({ request, params }) =>
      deny(request, STAFF) ??
      idempotent(request, () => {
        const d = dishes().find((x) => x.id === params.id);
        if (!d) return problem(404, "tiffin.not_found", "No such dish");
        d.status = "draft";
        d.decided_at = null;
        d.decided_by = null;
        d.note = null;
        return HttpResponse.json(d);
      }),
  ),
];
