// Small helpers shared by the fleet and partner pages (routes, deliveries, vehicles, partners, fairness).

/** The two delivery slots of the pilot hub. Slots are hub configuration (M6); until that API is wired both are fixed here. */
export const SLOTS = [
  { value: "am", label: "8 to 10 AM" },
  { value: "pm", label: "5 to 7 PM" },
];

const TZ = "Asia/Kolkata";

/**
 * "8:07 AM" for an ISO timestamp, in India time whatever the server's zone is.
 * @param {string | null | undefined} iso
 */
export function formatTime(iso) {
  if (!iso) return "";
  return new Intl.DateTimeFormat("en-IN", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: TZ,
  })
    .format(new Date(iso))
    .replace(/\b(am|pm)\b/i, (m) => m.toUpperCase());
}

/**
 * "Tue 6 Oct" for an ISO date or timestamp.
 * @param {string} iso
 */
export function formatDay(iso) {
  const parts = new Intl.DateTimeFormat("en-IN", {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone: TZ,
  }).formatToParts(new Date(iso));
  const get = (/** @type {string} */ type) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("weekday")} ${get("day")} ${get("month")}`;
}

/**
 * "18.6 km" for metres.
 * @param {number} metres
 */
export function formatKm(metres) {
  return `${(metres / 1000).toFixed(1)} km`;
}

/** @param {number} n @param {string} one @param {string} many */
export const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
