// ISO weeks (Monday to Sunday, IST) for the payout screens: M5 names a payout's week "2027-W03" (docs/modules/M5-billing.md §3.5)
// and `weekly_payouts` pays the week that ended last Sunday on Monday 10:00 IST. No imports, so server and client code can both use it.

const DAY = 86_400_000;

/** The India calendar date of a moment, "2026-10-14". @param {number} ms */
export const istDate = (ms) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(ms);

/**
 * The ISO week of a calendar date, with its Monday and Sunday.
 * @param {string} ymd "2026-10-14"
 * @returns {{ week: string, from: string, to: string }}
 */
export function isoWeekOf(ymd) {
  const day = new Date(`${ymd}T00:00:00Z`);
  const monday = new Date(day.getTime() - ((day.getUTCDay() + 6) % 7) * DAY);
  // The week belongs to the year of its Thursday.
  const thursday = new Date(monday.getTime() + 3 * DAY);
  const jan1 = Date.UTC(thursday.getUTCFullYear(), 0, 1);
  const n = Math.floor((thursday.getTime() - jan1) / (7 * DAY)) + 1;
  return {
    week: `${thursday.getUTCFullYear()}-W${String(n).padStart(2, "0")}`,
    from: monday.toISOString().slice(0, 10),
    to: new Date(monday.getTime() + 6 * DAY).toISOString().slice(0, 10),
  };
}

/**
 * The last `count` weeks that have ended, newest first: the first one is the week the next Monday run pays (or just paid).
 * @param {number} now
 * @param {number} count
 */
export function recentWeeks(now, count) {
  const today = new Date(`${istDate(now)}T00:00:00Z`).getTime();
  return Array.from({ length: count }, (_, i) =>
    isoWeekOf(new Date(today - (i + 1) * 7 * DAY).toISOString().slice(0, 10)),
  );
}

/** The week of an ISO date range label, "5 to 11 Oct". @param {{ from: string, to: string }} w */
export function weekRange(w) {
  const m = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const [, fm, fd] = w.from.split("-").map(Number);
  const [, tm, td] = w.to.split("-").map(Number);
  return fm === tm ? `${fd} to ${td} ${m[tm - 1]}` : `${fd} ${m[fm - 1]} to ${td} ${m[tm - 1]}`;
}

/**
 * The Monday 10:00 IST run that follows a week, as an ISO timestamp.
 * @param {{ to: string }} w
 */
export function runAfter(w) {
  const monday = new Date(new Date(`${w.to}T00:00:00Z`).getTime() + DAY);
  return `${monday.toISOString().slice(0, 10)}T10:00:00+05:30`;
}
