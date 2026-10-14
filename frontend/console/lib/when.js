/**
 * Date and time wording for the console pages. Delivery dates travel as ISO days ("2026-10-06") and
 * are shown the way the designs write them: "Tue 6 Oct". Times are shown in IST, the hub's zone.
 */

const day = new Intl.DateTimeFormat("en-IN", {
  weekday: "short",
  day: "numeric",
  month: "short",
  timeZone: "UTC",
});
const clock = new Intl.DateTimeFormat("en-IN", {
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
  timeZone: "Asia/Kolkata",
});
const dayTime = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
  timeZone: "Asia/Kolkata",
});

/**
 * "2026-10-06" → "Tue 6 Oct".
 * @param {string} iso a calendar day, YYYY-MM-DD
 */
export function dayLabel(iso) {
  return day
    .format(new Date(`${iso}T00:00:00Z`))
    .replace(",", "")
    .replace("Sept", "Sep");
}

/**
 * "2026-10-06" → "Tuesday".
 * @param {string} iso a calendar day, YYYY-MM-DD
 */
export function weekdayName(iso) {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-IN", {
    weekday: "long",
    timeZone: "UTC",
  });
}

/**
 * "2026-10-05T21:00:00+05:30" → "Mon 9 PM".
 * @param {string} iso
 */
export function weekdayClock(iso) {
  const wd = new Date(iso).toLocaleDateString("en-IN", {
    weekday: "short",
    timeZone: "Asia/Kolkata",
  });
  return `${wd} ${clockLabel(iso)}`;
}

/**
 * "2026-10-05T21:00:00+05:30" → "9 PM" (or "9:30 PM").
 * @param {string} iso
 */
export function clockLabel(iso) {
  return clock
    .format(new Date(iso))
    .replace(":00", "")
    .replace(/\s?([ap])m/i, (_, x) => ` ${x.toUpperCase()}M`);
}

/**
 * "2026-10-02T10:15:00+05:30" → "2 Oct, 10:15 AM".
 * @param {string} iso
 */
export function dayTimeLabel(iso) {
  return dayTime
    .format(new Date(iso))
    .replace(/\s?([ap])m/i, (_, x) => ` ${x.toUpperCase()}M`)
    .replace(/^(\d+ \w+) at /, "$1, ");
}

/**
 * How long from `from` to `to`: "2 h 14 min", "45 min", or "now" once it has passed.
 * @param {string} from ISO instant
 * @param {string} to ISO instant
 */
export function timeLeft(from, to) {
  const minutes = Math.round((new Date(to).getTime() - new Date(from).getTime()) / 60000);
  if (minutes <= 0) return "now";
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m} min`;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

/**
 * The IST calendar day of an instant, as YYYY-MM-DD.
 * @param {string} iso
 */
export function istDay(iso) {
  return new Date(iso).toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
}

/**
 * "2026-10-05T10:42:00+05:30" → "10:42" (24-hour, IST), the way payment tables show times.
 * @param {string} iso
 */
export function clock24(iso) {
  return new Date(iso).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Kolkata",
  });
}

/**
 * "Today, 10:42" when `iso` is on the same IST day as `asOf`, else "4 Oct, 10:42" style.
 * @param {string} iso
 * @param {string} asOf the server's current instant
 */
export function whenLabel(iso, asOf) {
  const day = istDay(iso) === istDay(asOf) ? "Today" : dayLabel(istDay(iso));
  return `${day}, ${clock24(iso)}`;
}

/**
 * "2026-09-28" → "28 Sep" (no weekday).
 * @param {string} iso a calendar day, YYYY-MM-DD
 */
export function dateLabel(iso) {
  return dayLabel(iso).split(" ").slice(1).join(" ");
}
