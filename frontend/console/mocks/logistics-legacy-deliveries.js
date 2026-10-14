// Legacy Deliveries mock (a per-bag proposal the M7 spec never had), kept only until the Deliveries page moves to the M7
// contract; the commit that does so deletes this file. GET /v1/deliveries?hub_id=.
import { HttpResponse, http } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { mockStore } from "./util.js";

const OLD_DAY = "2026-10-06";
/** @param {string} hhmm */
const oldAt = (hhmm) => `${OLD_DAY}T${hhmm}:00+05:30`;

const OLD_HUB = { id: KHARGHAR_HUB_ID };

// ---- Deliveries: every bag of the day, from handover to the door ------------------------------------------------

/** @typedef {"door" | "lobby" | "with_captain" | "issue"} BagState */

/**
 * @param {string} code @param {string} flat @param {string} society_id @param {string} society
 * @param {BagState} state @param {string} state_text @param {string} proof @param {string} time
 * @param {{ text: string, href?: string } | null} [next]
 */
const oldBagRow = (
  code,
  flat,
  society_id,
  society,
  state,
  state_text,
  proof,
  time,
  next = null,
) => ({
  bag: code,
  flat,
  society_id,
  society,
  state,
  state_text,
  proof,
  at: oldAt(time),
  next,
});

/** Flats of Sunrise Towers' three towers, skipping the ones the story names. */
function* oldFlats(/** @type {Set<string>} */ skip) {
  for (let unit = 1; unit <= 3; unit++) {
    for (let floor = 1; floor <= 12; floor++) {
      for (const tower of ["A", "B", "C"]) {
        const flat = `${tower}-${floor}${String(unit).padStart(2, "0")}`;
        if (!skip.has(flat)) yield flat;
      }
    }
  }
}

function oldBuildDeliveries() {
  const named = [
    oldBagRow(
      "B-218",
      "B-1104",
      "sunrise-towers",
      "Sunrise Towers",
      "issue",
      "Eggs broken",
      "Photo by Meena tai",
      "08:31",
      {
        text: "Approve refund",
        href: "/refunds",
      },
    ),
    oldBagRow(
      "B-230",
      "C-302",
      "sunrise-towers",
      "Sunrise Towers",
      "lobby",
      "In the lobby",
      "No one home, rang twice",
      "08:44",
      {
        text: "Back to the hub at 7 PM if not collected",
      },
    ),
    oldBagRow(
      "B-221",
      "B-603",
      "sunrise-towers",
      "Sunrise Towers",
      "lobby",
      "In the lobby",
      "Family chose lobby pickup",
      "08:15",
    ),
    oldBagRow(
      "B-224",
      "A-905",
      "sunrise-towers",
      "Sunrise Towers",
      "lobby",
      "In the lobby",
      "Family chose lobby pickup",
      "08:16",
    ),
    oldBagRow(
      "B-227",
      "A-407",
      "sunrise-towers",
      "Sunrise Towers",
      "lobby",
      "In the lobby",
      "Family chose lobby pickup",
      "08:18",
    ),
    oldBagRow(
      "B-233",
      "C-1002",
      "sunrise-towers",
      "Sunrise Towers",
      "lobby",
      "In the lobby",
      "Family chose lobby pickup",
      "08:21",
    ),
  ];
  const taken = new Set(named.map((b) => b.flat));
  const free = [...oldFlats(taken)];
  const taken_codes = new Set(named.map((b) => b.bag));
  /** @type {ReturnType<typeof oldBagRow>[]} */
  const door = [];
  for (let n = 201; door.length < 40; n++) {
    const code = `B-${n}`;
    if (taken_codes.has(code)) continue;
    const minute = 9 + Math.floor(door.length * 0.55);
    door.push(
      oldBagRow(
        code,
        /** @type {string} */ (free[door.length]),
        "sunrise-towers",
        "Sunrise Towers",
        "door",
        "At the door",
        "Scanned by Meena tai",
        `08:${String(minute).padStart(2, "0")}`,
      ),
    );
  }
  const krishnaFlats = [
    "B-205",
    "A-101",
    "A-203",
    "A-306",
    "B-102",
    "B-301",
    "B-404",
    "C-105",
    "C-207",
    "C-308",
    "D-110",
    "D-212",
  ];
  const krishna = krishnaFlats.map((flat, i) =>
    oldBagRow(
      `K-${101 + i}`,
      flat,
      "krishna-kunj",
      "Krishna Kunj",
      "with_captain",
      "With the captain",
      i === 4 ? "Methi refunded ₹20 to wallet" : "Crate K-1 handed over",
      "08:42",
      { text: "Door scan pending" },
    ),
  );
  return [...named, ...door, ...krishna];
}

const oldDeliveries = () => mockStore("logistics-deliveries-legacy", oldBuildDeliveries);

/** @param {ReturnType<typeof oldBagRow>[]} items */
function oldSummarise(items) {
  const count = (/** @type {BagState} */ s) => items.filter((b) => b.state === s).length;
  return {
    at_door: count("door"),
    in_lobby: count("lobby"),
    problems: count("issue"),
    lobby_collect_by: oldAt("19:00"),
    with_captain: [
      {
        society_id: "krishna-kunj",
        society: "Krishna Kunj",
        bags: count("with_captain"),
        handed_over_at: oldAt("08:42"),
      },
    ],
  };
}

export const legacyDeliveryHandlers = [
  // GET /v1/deliveries?hub_id=&date= (proposal; M7 only lists the per-order write endpoints). One row per bag with its
  // proof. Customer phone numbers are never part of it.
  http.get("*/v1/deliveries", ({ request }) => {
    const hub = new URL(request.url).searchParams.get("hub_id") ?? OLD_HUB.id;
    const items = hub === OLD_HUB.id ? oldDeliveries() : [];
    return HttpResponse.json({
      date: OLD_DAY,
      as_of: oldAt("09:40"),
      summary: oldSummarise(items),
      items,
      next_cursor: null,
    });
  }),
];
