import { shortDate, weekdayName } from "@/features/subscriptions/format.js";

/** "Tue 6 Oct" / "मंगल 6 अक्टू॰" from an ISO date, (shortDate uses Latin digits in every language). @param {string} iso @param {string} lang */
export const dayLabel = (iso, lang) => `${weekdayName(iso, lang)} ${shortDate(iso, lang)}`;

/** A stop is finished once it is handed over or skipped. @param {import("./queries.js").Stop} stop */
export const stopDone = (stop) => stop.status === "handed_over" || stop.status === "skipped";

/** The first stop not finished yet, or null when every stop is done. @param {import("./queries.js").Route} route */
export const nextStop = (route) => route.stops.find((s) => !stopDone(s)) ?? null;

/** The slot the route delivers in: the earliest start to the latest end of its stops. @param {import("./queries.js").Route} route */
export function routeSlot(route) {
  const slots = route.stops.flatMap((s) => (s.slot ? [s.slot] : []));
  if (slots.length === 0) return null;
  return {
    from: slots.map((s) => s.start).sort()[0],
    to:
      slots
        .map((s) => s.end)
        .sort()
        .at(-1) ?? "",
  };
}

/** "8:05" from "08:05", as the field screens write stop times (the slot already says AM or PM). @param {string} hhmm */
export const clock = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, "0")}`;
};
