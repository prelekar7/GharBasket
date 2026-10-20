import { useState } from "react";

// What the screens derive from a real CycleOut, which has no hub, no price alerts and no per-meal
// reasons: the planner's price swaps are lines with `reason_code: "price_swap"`.

/**
 * A swap Autopilot made on price, for the "Prices moved this week" banner (design Core-B4).
 * @typedef {{ product_id: string, item: string, substitute: string, pct: number | null, keep_delta_paise: number, original: import("./queries.js").Replaced }} PriceSwap
 * `item` is what got dearer, `substitute` what the box uses instead, and `keep_delta_paise` what
 * keeping the original would add to the box.
 */

/** @param {import("./queries.js").Cycle} cycle @returns {PriceSwap[]} */
export const priceSwaps = (cycle) =>
  cycle.lines.flatMap((l) =>
    l.reason_code === "price_swap" && l.replaces
      ? [
          {
            product_id: l.product_id,
            item: l.replaces.name,
            substitute: l.name,
            pct: typeof l.reason_params?.pct === "number" ? l.reason_params.pct : null,
            keep_delta_paise: l.replaces.unit_price_paise * l.qty - l.total_paise,
            original: l.replaces,
          },
        ]
      : [],
  );

/** The status pill each cycle status wears. @type {Record<string, "planned" | "preparing" | "ready" | "out" | "delivered" | "issue">} */
export const PILL = {
  draft: "planned",
  awaiting_approval: "preparing",
  approved: "ready",
  skipped: "planned",
  locked: "out",
  cancelled: "issue",
  payment_failed: "issue",
};

/** What a line's swap or replacement cost before, for the up/saves chip. @param {import("./queries.js").BoxLine} l */
export const swappedFromPaise = (l) =>
  l.replaces ? l.replaces.unit_price_paise * l.qty : undefined;

const KEY = "gb-dismissed-swaps";
/** @returns {Record<string, string[]>} */
const read = () => {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "{}");
  } catch {
    return {};
  }
};

/**
 * The swaps whose banner the person closed with OK. The API has no call for it, so it is kept on
 * this phone, per cycle; a changed box brings a new banner only for a new swap.
 * @param {string | null | undefined} cycleId
 */
export function useDismissedSwaps(cycleId) {
  const [all, setAll] = useState(read);
  const ids = (cycleId && all[cycleId]) || [];
  return {
    dismissed: ids,
    /** @param {string} productId */
    dismiss: (productId) => {
      if (!cycleId) return;
      const next = { ...read(), [cycleId]: [...ids, productId] };
      try {
        localStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        // private window or blocked storage: the banner just comes back next visit
      }
      setAll(next);
    },
  };
}
