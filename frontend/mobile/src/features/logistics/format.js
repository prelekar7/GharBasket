import { shortDate, weekdayName } from "@/features/subscriptions/format.js";

/** "Tue 6 Oct" / "मंगल 6 अक्टू॰" from an ISO date, (shortDate uses Latin digits in every language). @param {string} iso @param {string} lang */
export const dayLabel = (iso, lang) => `${weekdayName(iso, lang)} ${shortDate(iso, lang)}`;

/** The first stop not handed over yet, or null when every stop is done. @param {import("./queries.js").Route} route */
export const nextStop = (route) => route.stops.find((s) => s.status !== "done") ?? null;

/** Directions in the phone's maps app. @param {{ lat: number, lng: number }} p */
export const mapsLink = (p) =>
  `https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lng}&travelmode=driving`;

/** "8:05" from "08:05", as the field screens write stop times (the slot already says AM or PM). @param {string} hhmm */
export const clock = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, "0")}`;
};
