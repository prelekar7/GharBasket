// Tiffin menu approval mocks (M2 `catalog.tiffin_menus`, M8 kitchens). The spec names the table and
// the `tiffin_menus:write` permission (kitchen proposes, hub manager approves) but no paths, so the
// paths here are our proposal. The price families pay is the catalog price; the kitchen's pay is
// fixed by GharBasket. Neither is ever computed by the console or by an LLM.
import { HttpResponse, http } from "msw";
import { deny, istDay } from "./adminShared.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { idempotent, mockStore, problem } from "./util.js";

/**
 * @typedef {{ id: string, hub_id: string, name: string, components: string, kitchen: { id: string, name: string },
 *   meal: "lunch" | "dinner", tags: string[], price_paise: number, kitchen_pay_paise: number,
 *   contains: string[], fasting_ok: boolean, fasting_note: string, daily_capacity: number,
 *   status: "review" | "live" | "back", is_new: boolean, proposed_at: string,
 *   decided_at: string | null, decided_by: string | null, note: string | null }} TiffinDish
 */

/** @type {() => TiffinDish[]} */
const seed = () => [
  {
    id: "puran-poli-thali",
    hub_id: KHARGHAR_HUB_ID,
    name: "Puran poli thali",
    components: "Puran poli, katachi amti, rice, salad",
    kitchen: { id: "u-lata", name: "Lata Shinde" },
    meal: "lunch",
    tags: ["Vegetarian", "festival"],
    price_paise: 11000,
    kitchen_pay_paise: 7000,
    contains: ["Wheat", "jaggery", "toor dal"],
    fasting_ok: false,
    fasting_note: "Not suitable (has grains)",
    daily_capacity: 20,
    status: "review",
    is_new: true,
    proposed_at: istDay(-1, 17, 40),
    decided_at: null,
    decided_by: null,
    note: null,
  },
  {
    id: "sabudana-khichdi-upvas",
    hub_id: KHARGHAR_HUB_ID,
    name: "Sabudana khichdi (upvas)",
    components: "Sabudana, groundnuts, potato, curd",
    kitchen: { id: "u-lata", name: "Lata Shinde" },
    meal: "lunch",
    tags: ["Fasting", "no onion or garlic"],
    price_paise: 11000,
    kitchen_pay_paise: 7000,
    contains: ["Sabudana", "groundnuts", "potato", "curd"],
    fasting_ok: true,
    fasting_note: "Suitable (no grains)",
    daily_capacity: 20,
    status: "review",
    is_new: true,
    proposed_at: istDay(-1, 17, 52),
    decided_at: null,
    decided_by: null,
    note: null,
  },
  {
    id: "varan-bhaat-thali",
    hub_id: KHARGHAR_HUB_ID,
    name: "Varan-bhaat thali",
    components: "Approved 28 Sep",
    kitchen: { id: "u-lata", name: "Lata Shinde" },
    meal: "lunch",
    tags: ["Vegetarian"],
    price_paise: 11000,
    kitchen_pay_paise: 7000,
    contains: ["Rice", "toor dal", "ghee"],
    fasting_ok: false,
    fasting_note: "Not suitable (has grains)",
    daily_capacity: 20,
    status: "live",
    is_new: false,
    proposed_at: istDay(-9, 11, 0),
    decided_at: istDay(-8, 10, 15),
    decided_by: "Vikram Desai",
    note: null,
  },
  {
    id: "rajma-chawal-thali",
    hub_id: KHARGHAR_HUB_ID,
    name: "Rajma-chawal thali",
    components: "Rajma, jeera rice, kachumber, papad",
    kitchen: { id: "u-lata", name: "Lata Shinde" },
    meal: "lunch",
    tags: ["Vegetarian"],
    price_paise: 12000,
    kitchen_pay_paise: 7500,
    contains: ["Rice", "rajma", "curd"],
    fasting_ok: false,
    fasting_note: "Not suitable (has grains)",
    daily_capacity: 20,
    status: "live",
    is_new: false,
    proposed_at: istDay(-14, 12, 0),
    decided_at: istDay(-13, 9, 30),
    decided_by: "Vikram Desai",
    note: null,
  },
  {
    id: "pithla-bhakri-thali",
    hub_id: KHARGHAR_HUB_ID,
    name: "Pithla-bhakri thali",
    components: "Pithla, jowar bhakri, thecha, onion",
    kitchen: { id: "u-lata", name: "Lata Shinde" },
    meal: "dinner",
    tags: ["Vegetarian", "gluten-free"],
    price_paise: 10000,
    kitchen_pay_paise: 6500,
    contains: ["Besan", "jowar"],
    fasting_ok: false,
    fasting_note: "Not suitable (has grains)",
    daily_capacity: 20,
    status: "live",
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
        if (d.status !== "review") return problem(409, "tiffin.not_waiting", "Already handled");
        d.status = "live";
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
        if (d.status !== "review") return problem(409, "tiffin.not_waiting", "Already handled");
        const note = String(body?.note ?? "").trim();
        if (!note)
          return problem(422, "request.invalid", "Tell the kitchen what to change", {
            errors: [{ field: "note", message: "Tell the kitchen what to change" }],
          });
        d.status = "back";
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
        d.status = "review";
        d.decided_at = null;
        d.decided_by = null;
        d.note = null;
        return HttpResponse.json(d);
      }),
  ),
];
