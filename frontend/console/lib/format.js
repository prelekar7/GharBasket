// Small formatting helpers shared by the hub pages. Everything is shown in India time, because a
// hub's day is Indian whatever the browser's time zone is.

const DAY = new Intl.DateTimeFormat("en-IN", {
  weekday: "short",
  day: "numeric",
  month: "short",
  timeZone: "Asia/Kolkata",
});

/**
 * "Tue 6 Oct" for an ISO date ("2026-10-06") or timestamp.
 * @param {string} iso
 */
export function formatDay(iso) {
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00+05:30` : iso);
  return DAY.format(d).replace(",", "");
}

/**
 * Plural helper: `plural(1, "bag")` is "1 bag", `plural(2, "bag")` is "2 bags".
 * @param {number} n @param {string} word
 */
export const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
