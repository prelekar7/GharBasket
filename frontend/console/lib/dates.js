const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * "2026-09-07" → "7 Sep". Reads the text, so the month never shifts with a time zone, and avoids
 * Intl's "Sept" so dates match the designs.
 * @param {string} iso a date (or date-time) in ISO format
 */
export function shortDate(iso) {
  const [, month, day] = iso.slice(0, 10).split("-").map(Number);
  return `${day} ${MONTHS[month - 1]}`;
}

const clock = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZone: "Asia/Kolkata",
});

/**
 * An instant in Indian time: "6 Oct, 12:28 am". Fixed to Asia/Kolkata so the server and the browser
 * render the same text.
 * @param {string} iso
 */
export function dayAndTime(iso) {
  const parts = Object.fromEntries(
    clock.formatToParts(new Date(iso)).map((p) => [p.type, p.value]),
  );
  return `${Number(parts.day)} ${MONTHS[Number(parts.month) - 1]}, ${parts.hour}:${parts.minute} ${parts.dayPeriod}`;
}
