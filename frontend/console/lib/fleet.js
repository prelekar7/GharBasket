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
 * "8:07 AM" for an IST clock time as the API sends it ("08:07"), the way `formatTime` shows a timestamp.
 * @param {string | null | undefined} hhmm
 */
export function formatClock(hhmm) {
  if (!hhmm) return "";
  const [h, m] = hhmm.split(":").map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
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

/** An Idempotency-Key for one user intent; made when a form renders and reused if the same request is retried. */
export function newKey() {
  return (
    globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
  );
}

/** The vehicle kinds of M7 §3.1: boxes and add-ons ride cargo three-wheelers, tiffins ride two-wheelers. */
export const KINDS = [
  { value: "cargo_3w", label: "Electric three-wheeler" },
  { value: "two_wheeler", label: "Two-wheeler with box" },
];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * "12 Sep" for an ISO date or timestamp (always three-letter months, whatever the browser's locale data says).
 * @param {string} iso
 */
export function formatDate(iso) {
  const [, month, day] = new Date(iso).toLocaleDateString("en-CA", { timeZone: TZ }).split("-");
  return `${Number(day)} ${MONTHS[Number(month) - 1]}`;
}

/**
 * "₹3,290" for integer paise, for server code that cannot import `@gharbasket/ui` (Client Components use `MoneyText`).
 * @param {number} paise
 */
export function rupees(paise) {
  return `₹${(paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}
