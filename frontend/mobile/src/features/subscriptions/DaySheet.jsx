import { Button, Chip, formatPaise, Sheet, toast } from "@gharbasket/ui";
import { useTranslation } from "react-i18next";
import { useAddTiffin } from "@/features/orders/queries.js";
import { clockTime, istClock, reasonText, shortDate, weekdayName, weekdayOf } from "./format.js";
import { useSwapMeal } from "./queries.js";

/**
 * One day of the week: each meal with why it was planned and "Swap meal", and a home cook's
 * tiffin to add while the week is still open (design Core-W2, M3, M4).
 * @param {{ cycle: import("./queries.js").Cycle, index: number | null, onClose: () => void, editable: boolean, onError: (e: unknown) => void }} props
 */
export function DaySheet({ cycle, index, onClose, editable, onError }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const swap = useSwapMeal();
  const tiffin = useAddTiffin();
  const day = index === null ? null : cycle.meal_plan.days[index];
  const offer = day?.tiffin_offer;
  const orderBy = `${weekdayName(cycle.cutoff_at, lang)} ${clockTime(istClock(cycle.cutoff_at), lang)}`;

  return (
    <Sheet
      open={!!day}
      onOpenChange={(open) => !open && onClose()}
      title={day ? `${t(`days.short.d${weekdayOf(day.date)}`)}, ${shortDate(day.date, lang)}` : ""}
      footer={
        <Button variant="secondary" size="lg" block onPress={onClose}>
          {t("week.day.done")}
        </Button>
      }
    >
      {day && (
        <div className="flex flex-col gap-3">
          {day.meals.map((m) => (
            <section key={m.slot} className="flex flex-col gap-1.5 border-b border-line pb-3">
              <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
                {t(`week.slot.${m.slot}`)}
              </span>
              <div className="flex items-center justify-between gap-3">
                <strong>{m.name}</strong>
                {editable && !m.tiffin && (
                  <Button
                    variant="quiet"
                    isPending={swap.isPending && swap.variables?.slot === m.slot}
                    onPress={() =>
                      swap.mutate(
                        { cycle, date: day.date, slot: m.slot },
                        { onSuccess: () => toast(t("week.day.swapped")), onError },
                      )
                    }
                  >
                    {t("week.day.swap")}
                  </Button>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                {m.tiffin && <Chip kind="reason">{t("week.day.tiffin")}</Chip>}
                {m.reason_code && !m.tiffin && (
                  <Chip kind="reason">
                    {reasonText(t, { code: m.reason_code, params: m.reason_params ?? null })}
                  </Chip>
                )}
              </div>
            </section>
          ))}
          {editable && offer && (
            <section className="flex flex-col gap-2 rounded-md bg-surface px-4 py-3">
              <strong>{t(`week.day.noTime.${offer.slot}`)}</strong>
              <p className="text-sm leading-5 text-fg-secondary">
                {t("week.day.offer", {
                  cook: offer.cook.name,
                  dish: offer.dish.name,
                  price: formatPaise(offer.price_paise),
                  when: orderBy,
                })}
              </p>
              <div>
                <Button
                  variant="secondary"
                  isPending={tiffin.isPending}
                  onPress={() =>
                    tiffin.mutate(
                      { date: day.date, slot: offer.slot, kitchenId: offer.kitchen_id },
                      {
                        onSuccess: () =>
                          toast(
                            t("week.day.added", { dish: offer.dish.name, cook: offer.cook.name }),
                          ),
                        onError,
                      },
                    )
                  }
                >
                  {t("week.day.addTiffin")}
                </Button>
              </div>
            </section>
          )}
        </div>
      )}
    </Sheet>
  );
}
