// Display helpers for the admin pages. Pure, so server and client components can both use them.

/**
 * "+919800001008" → "+91 98000 01008". Only for a number the admin typed in to look someone up.
 * @param {string} e164
 */
export function formatPhone(e164) {
  const d = e164.replace(/^\+91/, "");
  return `+91 ${d.slice(0, 5)} ${d.slice(5)}`;
}

/**
 * "+919800001008" → "+91 ••••• •1008": lists never show the whole number.
 * @param {string} e164
 */
export function maskPhone(e164) {
  return `+91 ••••• •${e164.slice(-4)}`;
}

const IST = "Asia/Kolkata";
const day = new Intl.DateTimeFormat("en-CA", { timeZone: IST });
const clock = new Intl.DateTimeFormat("en-IN", {
  timeZone: IST,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const short = new Intl.DateTimeFormat("en-GB", { timeZone: IST, day: "numeric", month: "short" });

/**
 * "Today, 10:42", "Yesterday", "2 Oct": how recently something happened, in India time.
 * @param {string} iso
 * @param {number} nowMs the server's now, so the server and the browser agree
 */
export function whenLabel(iso, nowMs) {
  const then = new Date(iso);
  const today = day.format(new Date(nowMs));
  const yesterday = day.format(new Date(nowMs - 86_400_000));
  const d = day.format(then);
  if (d === today) return `Today, ${clock.format(then)}`;
  if (d === yesterday) return "Yesterday";
  return short.format(then);
}

/**
 * "Signed in today" / "yesterday" / "on 2 Oct".
 * @param {string | null} iso
 * @param {number} nowMs
 */
export function lastSeenLabel(iso, nowMs) {
  if (!iso) return "never signed in";
  const d = day.format(new Date(iso));
  if (d === day.format(new Date(nowMs))) return "signed in today";
  if (d === day.format(new Date(nowMs - 86_400_000))) return "signed in yesterday";
  return `signed in on ${short.format(new Date(iso))}`;
}

/**
 * The 10 digits of an Indian mobile number typed any usual way ("98000 01008", "+91 9800001008",
 * "09800001008"), or null when it is not one.
 * @param {string} text
 */
export function tenDigits(text) {
  const d = text.replace(/\D/g, "").replace(/^(91|0)(?=\d{10}$)/, "");
  return /^[6-9]\d{9}$/.test(d) ? d : null;
}
