// M7 captain mocks: the day at Sunrise Towers (designs: Tue 6 Oct, 46 bags in 3 crates, 4 for
// lobby pickup), the crate handover and each bag's outcome. Writes share applyAction() with the
// screen. Only the captain of the society may call them (X-Active-Role: captain:<society>).
import { HttpResponse, http } from "msw";
import { applyAction } from "@/features/captain/day.js";

const KEY = "gb-mock-captain";

/** Flats in walking order per wing; two families get a tiffin, four collect from the lobby. */
const WINGS = {
  A: [104, 203, 305, 402, 506, 604, 701, 708, 802, 905, 1001, 1103, 1203, 1302, 1404, 1501],
  B: [205, 304, 402, 507, 603, 702, 801, 806, 903, 1002, 1104, 1201, 1305, 1402],
  C: [102, 203, 302, 407, 505, 608, 702, 807, 901, 1003, 1106, 1204, 1301, 1405, 1502, 1601],
};
const TIFFIN = ["B-304", "B-402"];
const LOBBY = ["A-708", "A-1404", "B-603", "C-1003"];
const CRATES = [
  { id: "S-1", bags: 16 },
  { id: "S-2", bags: 15 },
  { id: "S-3", bags: 15 },
];

/** @returns {import("@/features/captain/day.js").CaptainDay} */
function seedDay() {
  const flats = Object.entries(WINGS).flatMap(([wing, ns]) =>
    ns.map((n) => [wing, `${wing}-${n}`]),
  );
  let crate = 0;
  let inCrate = 0;
  /** @type {import("@/features/captain/day.js").Bag[]} */
  const bags = flats.map(([wing, flat], i) => {
    if (inCrate === CRATES[crate].bags) [crate, inCrate] = [crate + 1, 0];
    inCrate += 1;
    return {
      code: `B-${196 + i}`, // B-402's bag is B-214, as on the designs
      order_id: `ord-1006-${String(i + 1).padStart(3, "0")}`,
      flat,
      wing,
      contents: TIFFIN.includes(flat) ? "box_tiffin" : "box",
      lines: flat === "B-402" ? 12 : 8 + ((i * 5) % 9),
      pickup: LOBBY.includes(flat) ? "lobby" : "door",
      crate: CRATES[crate].id,
      status: "planned",
      note: null,
    };
  });
  return {
    date: "2026-10-06",
    society: { id: "sunrise-towers", name: "Sunrise Towers" },
    captain_name: "Meena tai",
    stop: {
      id: "stop-1006-sunrise",
      eta: "2026-10-06T08:05:00+05:30",
      gate: 2,
      rider_name: "Arjun",
      handover_code: "4821",
      handed_over_at: null,
    },
    crates: CRATES.map((c) => ({ ...c, state: /** @type {const} */ ("waiting") })),
    bags,
  };
}

/** @returns {{ day: import("@/features/captain/day.js").CaptainDay, keys: Record<string, { ok: true }> }} */
const load = () => {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "null") ?? { day: seedDay(), keys: {} };
  } catch {
    return { day: seedDay(), keys: {} };
  }
};
const save = (/** @type {ReturnType<typeof load>} */ s) =>
  localStorage.setItem(KEY, JSON.stringify(s));
const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );

/** The captain of this society, or a problem. @param {Request} request */
const guard = (request) =>
  request.headers.get("x-active-role") === "captain:sunrise-towers"
    ? null
    : problem(403, "auth.forbidden_role", "Only this society’s captain can do that");

/**
 * A write: role check, Idempotency-Key, then `check` may refuse it before the day changes.
 * @param {(s: ReturnType<typeof load>, body: any, params: Record<string, any>) => Response | null} check
 */
const write =
  (check) =>
  async (/** @type {{ request: Request, params: Record<string, any> }} */ { request, params }) => {
    const refused = guard(request);
    if (refused) return refused;
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(
        400,
        "platform.idempotency_key_required",
        "Idempotency-Key header is required",
      );
    const s = load();
    if (s.keys[key]) return HttpResponse.json(s.keys[key]);
    const body = await request.json().catch(() => ({}));
    const bad = check(s, body, params);
    if (bad) return bad;
    s.day = applyAction(s.day, { path: new URL(request.url).pathname, body });
    s.keys[key] = { ok: true };
    save(s);
    return HttpResponse.json(s.keys[key], { status: 201 });
  };

/** The bag of an order still waiting for the captain, or a problem. */
const deliverable = (/** @type {ReturnType<typeof load>} */ s, /** @type {string} */ orderId) => {
  const bag = s.day.bags.find((b) => b.order_id === orderId);
  if (!bag) return problem(404, "logistics.delivery_not_found", "No such bag today");
  if (!s.day.stop.handed_over_at)
    return problem(409, "logistics.not_handed_over", "Receive the crates first");
  if (bag.status !== "planned" && bag.status !== "in_lobby")
    return problem(409, "logistics.already_closed", "This bag is already done");
  return null;
};

export const captainHandlers = [
  http.get(
    "*/v1/logistics/captain/today",
    ({ request }) => guard(request) ?? HttpResponse.json(load().day),
  ),

  http.post(
    "*/v1/stops/:id/handover",
    write((s, body, { id }) => {
      if (id !== s.day.stop.id) return problem(404, "logistics.stop_not_found", "No such stop");
      if (s.day.stop.handed_over_at)
        return problem(409, "logistics.already_handed_over", "These crates are already received");
      const known = s.day.crates.map((c) => c.id);
      const all = [...(body.crates ?? []), ...(body.missing ?? [])];
      return all.length === known.length && known.every((c) => all.includes(c))
        ? null
        : problem(422, "logistics.crates_mismatch", "Every crate is either received or missing");
    }),
  ),

  http.post(
    "*/v1/deliveries/:orderId/proof",
    write((s, _body, { orderId }) => deliverable(s, orderId)),
  ),

  http.post(
    "*/v1/deliveries/:orderId/undelivered",
    write((s, body, { orderId }) =>
      body.reason === "not_home" && ["lobby", "return"].includes(body.outcome)
        ? deliverable(s, orderId)
        : problem(422, "logistics.invalid_outcome", "Say what happens to the bag"),
    ),
  ),

  http.post(
    "*/v1/deliveries/:orderId/damage",
    write((s, body, { orderId }) =>
      typeof body.note === "string" && body.note.trim()
        ? deliverable(s, orderId)
        : problem(422, "logistics.note_required", "Say what is damaged"),
    ),
  ),
];

/** Dev helper: start the day again. */
export const resetCaptain = () => localStorage.removeItem(KEY);
