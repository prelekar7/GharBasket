import { shortDate, weekdayOf } from "@/features/subscriptions/format.js";

/** The India-time date of a UTC timestamp, "YYYY-MM-DD" (entries are UTC, the app shows IST). @param {string} at */
export const istDate = (at) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(at));

/**
 * Words for a ledger entry. A kind the app doesn't know yet gets a generic label, and a box debit
 * without a delivery date says only that it was a box.
 * @param {import("i18next").TFunction} t
 * @param {import("./queries.js").Entry} e
 * @param {string} lang
 */
export function entryLabel(t, e, lang) {
  const date = shortDate(istDate(e.at), lang);
  if (e.kind === "box_debit") {
    const day = e.ref?.type === "order" ? e.ref.delivery_date : null;
    return day
      ? t("wallet.entry.box", {
          day: `${t(`days.short.d${weekdayOf(day)}`)} ${shortDate(day, lang)}`,
        })
      : t("wallet.entry.boxNoDate", { date });
  }
  const known = [
    "topup",
    "topup_bonus",
    "refund",
    "adjustment",
    "refund_to_source",
    "refund_to_source_failed",
  ];
  return t(known.includes(e.kind) ? `wallet.entry.${e.kind}` : "wallet.entry.generic", { date });
}
