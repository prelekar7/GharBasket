/**
 * Integer money helpers for the console's server code and mocks. Money is integer paise and
 * percentages are basis points (1% = 100 bp); nothing here touches a float. Turning paise into text is
 * `formatPaise` from @gharbasket/ui, which Client Components import.
 */

/**
 * `amount × rate / per`, rounded half up, in integers. Use it for "24% of ₹620" (`per` 10_000, rate in
 * bp) and for "702 × ₹40" alike. Amounts here are never negative; a negative amount rounds half away
 * from zero.
 * @param {number} amount paise (or any whole number)
 * @param {number} rate whole number, e.g. basis points
 * @param {number} per whole number divisor, e.g. 10_000
 */
export function mulDivRound(amount, rate, per) {
  const product = Math.abs(amount * rate);
  const rounded = Math.floor((2 * product + per) / (2 * per));
  return amount * rate < 0 ? -rounded : rounded;
}

/** Rounds paise to a whole rupee (half up), the way a P&L statement rounds its lines. @param {number} paise */
export function toWholeRupee(paise) {
  return mulDivRound(paise, 1, 100) * 100;
}

/**
 * Reads what a person typed ("620", "1.2", "40.50") as a whole number scaled by 10^scale: with scale 2
 * "40.50" is 4050 (paise), and "1.2" is 120 (basis points of a percentage). Returns null for anything
 * that is not a plain non-negative decimal with at most `scale` decimals. Works on the text, so no float
 * is ever involved.
 * @param {string} text
 * @param {number} [scale]
 * @returns {number | null}
 */
export function parseDecimal(text, scale = 2) {
  const match = /^(\d{1,9})(?:\.(\d*))?$/.exec(text.trim().replace(/,/g, ""));
  if (!match) return null;
  const decimals = match[2] ?? "";
  if (decimals.length > scale) return null;
  return Number(match[1] + decimals.padEnd(scale, "0"));
}

/**
 * The text to show in an input for a scaled whole number: 4050 → "40.5", 62000 → "620".
 * @param {number} value
 * @param {number} [scale]
 */
export function toDecimalText(value, scale = 2) {
  const unit = 10 ** scale;
  const whole = Math.floor(value / unit);
  const fraction = String(value % unit)
    .padStart(scale, "0")
    .replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : String(whole);
}

/**
 * Masks a phone number for screens that may show only that a number exists: "+91 98••• ••012".
 * @param {string} phone digits, with or without a country code
 */
export function maskPhone(phone) {
  const digits = phone.replace(/\D/g, "").slice(-10);
  return `+91 ${digits.slice(0, 2)}••• ••${digits.slice(-3)}`;
}

const grouped = new Intl.NumberFormat("en-IN");

/**
 * Paise as rupees with both decimals, for unit-economics steps where ₹40.00 must line up with ₹7.44:
 * 14880 → "₹148.80", -700 → "−₹7.00". Built from the integer digits, not by dividing.
 * @param {number} paise
 */
export function formatPaiseExact(paise) {
  const abs = Math.abs(paise);
  const cents = String(abs % 100).padStart(2, "0");
  return `${paise < 0 ? "−" : ""}₹${grouped.format(Math.floor(abs / 100))}.${cents}`;
}
