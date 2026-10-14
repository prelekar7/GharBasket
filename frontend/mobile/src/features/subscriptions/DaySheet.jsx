import {
  Button,
  Chip,
  formatPaise,
  KnowYourCookCard,
  Sheet,
  Skeleton,
  toast,
} from "@gharbasket/ui";
import { PlusIcon } from "@phosphor-icons/react";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { usePublicPartner } from "@/features/partners/queries.js";
import { clockTime, istClock, reasonText, shortDate, weekdayName, weekdayOf } from "./format.js";
import { useSwapMeal } from "./queries.js";

/**
 * One day of the week: each meal with why it was planned and "Swap meal", and a home cook's
 * tiffin to book while the week is still open (designs Core-W2, HcTiffin; M3, M4).
 * @param {{ cycle: import("./queries.js").Cycle, index: number | null, onClose: () => void, editable: boolean, onError: (e: unknown) => void }} props
 */
export function DaySheet({ cycle, index, onClose, editable, onError }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const swap = useSwapMeal();
  const navigate = useNavigate();
  const day = index === null ? null : cycle.meal_plan.days[index];
  const offer = day?.tiffin_offer;
  const cook = usePublicPartner(editable ? offer?.kitchen_id : undefined, lang);
  const orderBy = `${weekdayName(cycle.cutoff_at, lang)} ${clockTime(istClock(cycle.cutoff_at), lang)}`;

  return (
    <Sheet
      open={!!day}
      onOpenChange={(open) => !open && onClose()}
      title={day ? `${t(`days.short.d${weekdayOf(day.date)}`)}, ${shortDate(day.date, lang)}` : ""}
      footer={
        <Button variant="primary" size="lg" block onPress={onClose}>
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
            <section className="flex flex-col gap-2.5 border-t border-line pt-3">
              <h2 className="text-lg leading-[26px] font-bold">
                {t(`week.day.noTime.${offer.slot}`)}
              </h2>
              {cook.isLoading && <Skeleton lines={3} />}
              {cook.data && (
                <KnowYourCookCard
                  name={cook.data.name}
                  kitchen={`${cook.data.kitchen}, ${cook.data.area}`}
                  since={cook.data.since}
                  specialty={cook.data.specialty}
                  fssai={cook.data.fssai_registered}
                  hygiene={cook.data.hygiene_trained}
                  labels={{
                    eyebrow: t("tiffin.knowCook"),
                    since: t("tiffin.since"),
                    fssai: t("tiffin.fssai"),
                    hygiene: t("tiffin.hygiene"),
                  }}
                />
              )}
              <p className="text-sm leading-5 text-fg-secondary">
                {t("week.day.offer", {
                  cook: offer.cook.name,
                  dish: offer.dish.name,
                  price: formatPaise(offer.price_paise),
                  when: orderBy,
                })}
              </p>
              <Button
                variant="secondary"
                block
                onPress={() => {
                  onClose();
                  navigate({ to: "/week/tiffin/$date", params: { date: day.date } });
                }}
              >
                <PlusIcon aria-hidden size={20} weight="bold" />
                {t("week.day.addTiffin")}
              </Button>
            </section>
          )}
        </div>
      )}
    </Sheet>
  );
}
