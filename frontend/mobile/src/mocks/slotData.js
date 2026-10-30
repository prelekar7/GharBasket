// The box slots of the delivery date and what each costs the van's route (M10 §3.8), for the green
// slot mocks. Pure data, so the cycle, the orders and the quote handlers can all read it.
const id = (/** @type {number} */ n) => `0199a000-0000-7000-8000-00000000c${n}`;

/** Slot ids as the hub's seed has them; the morning slot is the household's (orders.js BOX_SLOT). */
export const MORNING = "0199a000-0000-7000-8000-00000000c001";
/** @type {{ id: string, en: string, hi: string, start: string, end: string, available: number, cost: number, reason: "same_society" | "neighbour_stop" | "new_stop" | "new_trip", metres: number | null }[]} */
export const SLOTS = [
  {
    id: id(11),
    en: "Early 6-8",
    hi: "सुबह 6-8",
    start: "06:00:00",
    end: "08:00:00",
    available: 0,
    cost: 1900,
    reason: "new_stop",
    metres: 500,
  },
  {
    id: MORNING,
    en: "Morning 8-10",
    hi: "सुबह 8-10",
    start: "08:00:00",
    end: "10:00:00",
    available: 40,
    cost: 1800,
    reason: "same_society",
    metres: null,
  },
  {
    id: id(12),
    en: "Late morning 10-12",
    hi: "दोपहर 10-12",
    start: "10:00:00",
    end: "12:00:00",
    available: 12,
    cost: 300,
    reason: "neighbour_stop",
    metres: 400,
  },
  {
    id: id(13),
    en: "Evening 5-7",
    hi: "शाम 5-7",
    start: "17:00:00",
    end: "19:00:00",
    available: 25,
    cost: 2600,
    reason: "new_trip",
    metres: null,
  },
];
const MIN_SAVING = 500;
const MAX_SAVING = 1500;
const GREEN = ["same_society", "neighbour_stop"];

/** A dev switch: slot ids (comma separated) that have just filled up. */
export const FULL_KEY = "gb-mock-slot-full";
export const availableIn = (/** @type {(typeof SLOTS)[number]} */ s) =>
  (localStorage.getItem(FULL_KEY) ?? "").split(",").includes(s.id) ? 0 : s.available;

/** SlotRefOut for a slot id (the morning slot when it is unknown). */
export function slotRef(/** @type {string | undefined} */ slotId, /** @type {"en" | "hi"} */ lang) {
  const s = SLOTS.find((x) => x.id === slotId) ?? SLOTS[1];
  return { id: s.id, label: lang === "hi" ? s.hi : s.en, start: s.start, end: s.end };
}

/** The saving of moving from `from` to `to`: whole rupees of the route cost it removes, ₹5 at least, ₹15 at most. */
export const savingOf = (/** @type {string} */ from, /** @type {string} */ to) => {
  const a = SLOTS.find((s) => s.id === from)?.cost ?? 0;
  const b = SLOTS.find((s) => s.id === to)?.cost ?? 0;
  const raw = Math.floor(Math.min(MAX_SAVING, Math.max(0, a - b)) / 100) * 100;
  return from === to || raw < MIN_SAVING ? 0 : raw;
};

/** SlotOptionOut for every slot, by start time; `source` "unpriced" drops every price and reason. */
export function slotItems(
  /** @type {string} */ current,
  /** @type {"en" | "hi"} */ lang,
  /** @type {"ml" | "fallback" | "unpriced"} */ source = "ml",
) {
  const ranked = [...SLOTS].sort((a, b) => a.cost - b.cost).map((s) => s.id);
  return SLOTS.map((s) => {
    const priced = source !== "unpriced";
    return {
      slot: slotRef(s.id, lang),
      current: s.id === current,
      available: availableIn(s),
      green: priced && GREEN.includes(s.reason),
      saving_paise: priced ? savingOf(current, s.id) : 0,
      rank: priced ? ranked.indexOf(s.id) + 1 : null,
      reason_code: priced ? s.reason : "unpriced",
      reason_params: priced && s.metres ? { metres: s.metres } : null,
    };
  });
}

/** The moved slot and honoured saving kept in the mock cycle's stored state. */
export function movedSlot() {
  try {
    const s = JSON.parse(localStorage.getItem("gb-mock-cycle") ?? "null");
    return { slot_id: s?.slot_id ?? MORNING, green: s?.green ?? null };
  } catch {
    return { slot_id: MORNING, green: null };
  }
}
