// Tiffin menu review mocks, the real contract of M2 §3.4: `GET /v1/hubs/{id}/tiffin-menus` lists a hub's menus of
// every status, and `approve`, `send-back` and `reopen` answer the menu (TiffinMenuReview). A menu is one kitchen's
// offer for a date and meal. Statuses: submitted (waiting) → approved | sent_back; reopen takes an approved or
// sent-back menu back to submitted until its cut-off (`order_by`, 21:00 IST the day before). The writes need no
// Idempotency-Key (no money or order moves), so these handlers do not ask for one. The price is the menu's own;
// what the kitchen is paid is the hub's rate card (M8 §3.6, partners.js). The console never computes either.
import { HttpResponse, http } from "msw";
import { deny, istDay } from "./adminShared.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { PARTNER_ID } from "./partner-ids.js";
import { fieldError, mockStore, mockUuid, problem } from "./util.js";

/**
 * @typedef {{ id: string, hub_id: string, date: string, meal: "lunch" | "dinner",
 *   status: "submitted" | "approved" | "sent_back" | "withdrawn",
 *   kitchen: { partner_id: string, display_name: string | null },
 *   product: { id: string, slug: string, name: string, tags: string[] },
 *   dishes: { recipe_id: string, name: string }[], price_paise: number,
 *   rules: { jain_ok: boolean, no_onion_garlic: boolean, fasting_ok: boolean, diabetic_ok: boolean },
 *   contains: string[], order_by: string, submitted_at: string, decided_at: string | null,
 *   decided_by: { user_id: string, name: string | null } | null, note: string | null, version: number }} TiffinMenuReview
 */

const LATA = { partner_id: PARTNER_ID.lata, display_name: "Lata Shinde" };
const NO_RULES = { jain_ok: false, no_onion_garlic: false, fasting_ok: false, diabetic_ok: false };
const VIKRAM = { user_id: mockUuid("b510", 1), name: "Vikram Desai" };

/**
 * One seed menu, for the day after tomorrow so its cut-off (the evening before) is still ahead whenever the
 * mock server starts.
 * @param {number} n
 * @param {Omit<Partial<TiffinMenuReview>, "dishes"> & { name: string, dishes: string[] }} o
 * @returns {TiffinMenuReview}
 */
function menu(n, { name, dishes, ...o }) {
  return {
    id: mockUuid("b500", n),
    hub_id: KHARGHAR_HUB_ID,
    date: istDay(2, 0, 0).slice(0, 10),
    meal: "lunch",
    status: "submitted",
    kitchen: LATA,
    product: { id: mockUuid("b501", n), slug: `tiffin-${n}`, name, tags: [] },
    dishes: dishes.map((d, i) => ({ recipe_id: mockUuid("b502", n * 10 + i), name: d })),
    price_paise: 11000,
    rules: NO_RULES,
    contains: [],
    order_by: istDay(1, 21, 0),
    submitted_at: istDay(-1, 17, 40),
    decided_at: null,
    decided_by: null,
    note: null,
    version: 1,
    ...o,
  };
}

/** @type {() => TiffinMenuReview[]} */
const seed = () => [
  menu(1, {
    name: "Puran poli thali",
    dishes: ["Puran poli", "Katachi amti", "Steamed rice", "Salad"],
    product: {
      id: mockUuid("b501", 1),
      slug: "puran-poli-thali",
      name: "Puran poli thali",
      tags: ["festival"],
    },
    contains: ["Wheat", "Jaggery", "Toor dal"],
  }),
  menu(2, {
    name: "Sabudana khichdi (upvas)",
    dishes: ["Sabudana khichdi", "Peanut curd"],
    product: {
      id: mockUuid("b501", 2),
      slug: "sabudana-khichdi",
      name: "Sabudana khichdi (upvas)",
      tags: ["fasting"],
    },
    rules: { jain_ok: false, no_onion_garlic: true, fasting_ok: true, diabetic_ok: false },
    contains: ["Sabudana", "Groundnuts", "Potato", "Curd"],
    submitted_at: istDay(-1, 17, 52),
  }),
  menu(3, {
    name: "Varan-bhaat thali",
    dishes: ["Varan", "Steamed rice", "Ghee"],
    status: "approved",
    contains: ["Rice", "Toor dal", "Ghee"],
    submitted_at: istDay(-9, 11, 0),
    decided_at: istDay(-8, 10, 15),
    decided_by: VIKRAM,
    version: 2,
  }),
  menu(4, {
    name: "Rajma-chawal thali",
    dishes: ["Rajma", "Jeera rice", "Kachumber"],
    status: "approved",
    price_paise: 12000,
    contains: ["Rice", "Rajma", "Curd"],
    submitted_at: istDay(-14, 12, 0),
    decided_at: istDay(-13, 9, 30),
    decided_by: VIKRAM,
    version: 2,
  }),
  menu(5, {
    name: "Pithla-bhakri thali",
    dishes: ["Pithla", "Jowar bhakri", "Thecha"],
    meal: "dinner",
    status: "sent_back",
    price_paise: 10000,
    rules: { jain_ok: false, no_onion_garlic: false, fasting_ok: false, diabetic_ok: true },
    contains: ["Besan", "Jowar"],
    submitted_at: istDay(-3, 15, 0),
    decided_at: istDay(-2, 10, 5),
    decided_by: VIKRAM,
    note: "Less oil in the thecha, please.",
    version: 2,
  }),
];

const menus = () => mockStore("tiffin.menus", seed);
const STAFF = ["admin", "hub_manager", "catalog_editor"];
const WHO = { admin: "Neha Iyer", hub_manager: "Vikram Desai", catalog_editor: "Neha Iyer" };

/** The reviewer of a request, as the API names them. @param {Request} request */
function reviewer(request) {
  const role = /** @type {keyof typeof WHO} */ (request.headers.get("x-gb-role") ?? "admin");
  return { user_id: mockUuid("b510", role === "hub_manager" ? 1 : 2), name: WHO[role] };
}

const NO_SUCH = () => problem(404, "catalog.menu_not_found", "No such menu");
const closed = (/** @type {TiffinMenuReview} */ m) => Date.parse(m.order_by) < Date.now();

/** Waiting menus first (oldest submitted first), then by date and meal. @type {(a: TiffinMenuReview, b: TiffinMenuReview) => number} */
const reviewOrder = (a, b) =>
  Number(b.status === "submitted") - Number(a.status === "submitted") ||
  (a.status === "submitted" ? a.submitted_at.localeCompare(b.submitted_at) : 0) ||
  a.date.localeCompare(b.date) ||
  a.meal.localeCompare(b.meal);

/** Marks a menu decided (or, with `status: "submitted"`, reopened) and answers it. */
function decide(
  /** @type {TiffinMenuReview} */ m,
  /** @type {Request} */ request,
  /** @type {TiffinMenuReview["status"]} */ status,
  /** @type {string | null} */ note = null,
) {
  const reopened = status === "submitted";
  m.status = status;
  m.decided_at = reopened ? null : new Date().toISOString();
  m.decided_by = reopened ? null : reviewer(request);
  m.note = reopened ? null : note;
  m.version += 1;
  return HttpResponse.json(m, { headers: { "cache-control": "no-store" } });
}

export const tiffinHandlers = [
  http.get("*/v1/hubs/:hub/tiffin-menus", ({ request, params }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const q = new URL(request.url).searchParams;
    const status = q.get("status");
    const from = q.get("from") ?? istDay(0, 0, 0).slice(0, 10);
    const to = q.get("to") ?? istDay(7, 0, 0).slice(0, 10);
    if (to < from) return problem(422, "request.invalid", "`to` is before `from`");
    const items = menus()
      .filter(
        (m) =>
          m.hub_id === params.hub &&
          (!status || m.status === status) &&
          m.date >= from &&
          m.date <= to,
      )
      .sort(reviewOrder);
    return HttpResponse.json(
      { items, next_cursor: null },
      { headers: { "cache-control": "no-store" } },
    );
  }),

  http.post("*/v1/tiffin-menus/:id/approve", ({ request, params }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const m = menus().find((x) => x.id === params.id);
    if (!m) return NO_SUCH();
    if (m.status === "approved") return HttpResponse.json(m); // an approved menu again: 200, unchanged
    if (m.status !== "submitted")
      return problem(409, "catalog.menu_not_submitted", "Only a waiting menu can be approved");
    if (closed(m)) return problem(409, "catalog.menu_closed", "The cut-off has passed");
    if (m.price_paise <= 0)
      return problem(422, "catalog.menu_invalid", "The menu cannot be approved", {
        reasons: ["price_invalid"],
      });
    return decide(m, request, "approved");
  }),

  http.post("*/v1/tiffin-menus/:id/send-back", async ({ request, params }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const m = menus().find((x) => x.id === params.id);
    if (!m) return NO_SUCH();
    const body = /** @type {{ note?: unknown } | null} */ (await request.json().catch(() => null));
    const note = String(body?.note ?? "").trim();
    if (!note || note.length > 500)
      return problem(422, "request.invalid", "Tell the kitchen what to change", {
        errors: [fieldError("note", "Tell the kitchen what to change", "string_too_short")],
      });
    if (m.status !== "submitted")
      return problem(409, "catalog.menu_not_submitted", "Only a waiting menu can be sent back");
    return decide(m, request, "sent_back", note);
  }),

  // Undo of approve or send back: the menu returns to the waiting list until its cut-off.
  http.post("*/v1/tiffin-menus/:id/reopen", ({ request, params }) => {
    const refused = deny(request, STAFF);
    if (refused) return refused;
    const m = menus().find((x) => x.id === params.id);
    if (!m) return NO_SUCH();
    if (m.status !== "approved" && m.status !== "sent_back")
      return problem(409, "catalog.menu_not_decided", "This menu is not decided");
    if (closed(m)) return problem(409, "catalog.menu_closed", "The cut-off has passed");
    return decide(m, request, "submitted");
  }),
];
