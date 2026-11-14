import { MoneyText } from "@gharbasket/ui";
import { useTranslation } from "react-i18next";
import { shortDate } from "@/features/subscriptions/format.js";
import { dayLabel } from "./format.js";

/**
 * What the rider earned this week: the total, the days that paid, and what M5 paid out, which is
 * last week's payout (it is listed under the week it pays; design RiderEarnings; M8 §3.6). Every
 * part may be missing or empty; none of it can throw.
 * @param {{ data: import("@/features/partners/earnings.js").KindEarnings,
 *   lastPayouts?: import("@/features/partners/earnings.js").Payout[] }} props
 */
export function RiderEarningsView({ data, lastPayouts = [] }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const items = data.items ?? [];
  const seen = new Set();
  const payouts = [...(data.payouts ?? []), ...(lastPayouts ?? [])].filter(
    (p) => !seen.has(p.id) && seen.add(p.id),
  );
  return (
    <>
      <section className="flex flex-col gap-1 rounded-md bg-info-soft p-4">
        <span className="text-[15px] leading-[22px] text-fg-secondary">
          {data.from && data.to
            ? t("rider.earnings.recentRange", {
                from: shortDate(data.from, lang),
                to: shortDate(data.to, lang),
              })
            : t("rider.earnings.recent")}
        </span>
        <MoneyText paise={data.total_paise ?? 0} size="hero" />
        <span className="text-[15px] leading-[22px] text-fg-secondary">
          {t("rider.earnings.summary", { orders: data.units ?? 0 })}
        </span>
      </section>
      {items.length === 0 ? (
        <p className="text-[15px] text-fg-secondary">{t("rider.earnings.none")}</p>
      ) : (
        <ul className="m-0 list-none p-0">
          {items.map((d) => (
            <li
              key={d.date}
              className="flex items-baseline justify-between gap-3 border-b border-line py-3"
            >
              <span className="flex flex-col">
                <strong>{dayLabel(d.date, lang)}</strong>
                <span className="text-[15px] leading-[22px] text-fg-secondary">
                  {t("rider.earnings.line", { orders: d.units })}
                </span>
              </span>
              <MoneyText paise={d.amount_paise} />
            </li>
          ))}
        </ul>
      )}
      {payouts.length > 0 && (
        <ul className="m-0 list-none p-0">
          {payouts.map((p) => (
            <li
              key={p.id}
              className="flex items-baseline justify-between gap-3 border-b border-line py-3"
            >
              <span className="flex flex-col">
                <strong>
                  {p.period?.from && p.period?.to
                    ? t("rider.earnings.payoutPeriod", {
                        from: shortDate(p.period.from, lang),
                        to: shortDate(p.period.to, lang),
                      })
                    : t("rider.earnings.payoutWeek", { week: p.week })}
                </strong>
                <span className="text-[15px] leading-[22px] text-fg-secondary">
                  {t("rider.earnings.payoutStatus", { status: p.status })}
                </span>
                {p.status === "held" && p.hold_reason && (
                  <span className="text-[15px] leading-[22px] font-semibold text-fg">
                    {t(`rider.earnings.holdReason.${p.hold_reason}`)}
                  </span>
                )}
              </span>
              <MoneyText paise={p.amount_paise} />
            </li>
          ))}
        </ul>
      )}
      <p className="text-[15px] leading-[22px] text-fg-secondary">
        {t("rider.earnings.payout", { day: t("days.long.d0") })}
      </p>
    </>
  );
}
