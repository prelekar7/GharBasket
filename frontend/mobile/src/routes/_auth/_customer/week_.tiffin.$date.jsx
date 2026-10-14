import {
  Button,
  EmptyState,
  formatPaise,
  IconButton,
  KnowYourCookCard,
  MealCard,
  QtyStepper,
  Skeleton,
  TiffinSteam,
  toast,
} from "@gharbasket/ui";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import { createFileRoute, useNavigate, useRouter } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useAddTiffin } from "@/features/orders/queries.js";
import { usePublicPartner } from "@/features/partners/queries.js";
import {
  clockTime,
  istClock,
  shortDate,
  weekdayName,
  weekdayOf,
} from "@/features/subscriptions/format.js";
import { isEditable, useCurrentCycle, useTiffinOffers } from "@/features/subscriptions/queries.js";
import { ApiError } from "@/lib/api.js";

/** Book a home cook's tiffin for one day: who cooks it, the meal, how many, the total (design HcTiffin; M4, M8). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/week_/tiffin/$date")({
  component: Tiffin,
});

function Tiffin() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const router = useRouter();
  const navigate = useNavigate();
  const { date } = Route.useParams();
  const { data: c, isPending, isError, refetch } = useCurrentCycle(lang);
  const offers = useTiffinOffers(c);
  const offer = offers.data?.find((o) => o.date === date) ?? null;
  const cook = usePublicPartner(offer?.kitchen_id, lang);
  const book = useAddTiffin();
  const [qty, setQty] = useState(2);
  const editable = isEditable(c);

  const header = (
    <header className="-ml-3 flex items-center gap-1">
      <IconButton
        label={t("onboarding.back")}
        variant="quiet"
        onPress={() => router.history.back()}
      >
        <ArrowLeftIcon size={24} />
      </IconButton>
      <div className="flex flex-col">
        <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
          {t("tiffin.when", {
            day: `${t(`days.short.d${weekdayOf(date)}`)} ${shortDate(date, lang)}`,
            slot: t(`week.slot.${offer?.slot ?? "lunch"}`),
          })}
        </span>
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {t("tiffin.title")}
        </h1>
      </div>
    </header>
  );

  if (isPending || offers.isLoading || (offer && cook.isPending))
    return (
      <div className="flex flex-col gap-4">
        {header}
        <Skeleton lines={8} />
      </div>
    );
  if (isError || cook.isError)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <ScreenError
          onRetry={() => {
            refetch();
            cook.refetch();
          }}
        />
      </div>
    );
  // No week yet, booked already, or the week closed at the cut-off.
  if (!c || !offer || !editable || !cook.data)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <EmptyState
          illustration={<TiffinSteam className="h-auto w-28" />}
          title={t("tiffin.goneTitle")}
          body={t("tiffin.goneBody")}
          action={t("tiffin.backToWeek")}
          onAction={() => navigate({ to: "/week" })}
        />
      </div>
    );

  const k = cook.data;
  const orderBy = `${weekdayName(offer.order_by, lang)} ${clockTime(istClock(offer.order_by), lang)}`;
  const total = qty * offer.price_paise;

  return (
    <div className="flex min-h-[calc(100dvh-180px)] flex-col gap-3.5">
      {header}
      <KnowYourCookCard
        name={k.name}
        kitchen={`${k.kitchen}, ${k.area}`}
        specialty={k.specialty}
        fssai={k.fssai_registered}
        hygiene={k.hygiene_trained}
        labels={{
          eyebrow: t("tiffin.knowCook"),
          fssai: t("tiffin.fssai"),
          hygiene: t("tiffin.hygiene"),
        }}
      />
      <MealCard
        slot={t(`week.slot.${offer.slot}`)}
        name={offer.dish.name}
        items={offer.dish.items}
        cook={k.name}
        pricePaise={offer.price_paise}
        labels={{ cookedBy: t("tiffin.cookedBy") }}
      />
      <div>
        <QtyStepper
          label={t("tiffin.how")}
          value={qty}
          onChange={setQty}
          min={1}
          max={6}
          unit={t("tiffin.unit", { n: qty })}
          labels={{ decrease: t("box.qty.less"), increase: t("box.qty.more") }}
        />
      </div>
      <p className="text-sm leading-5 text-fg-secondary">
        {t("tiffin.note", { when: orderBy, cook: offer.cook.name, captain: c.captain?.name ?? "" })}
      </p>
      <div className="mt-auto">
        <Button
          variant="primary"
          size="lg"
          block
          isPending={book.isPending}
          onPress={() =>
            book.mutate(
              { date, slot: offer.slot, kitchenId: offer.kitchen_id, qty },
              {
                onSuccess: () => {
                  toast(t("week.day.added", { dish: offer.dish.name, cook: offer.cook.name }));
                  navigate({ to: "/week" });
                },
                onError: (e) =>
                  toast(
                    t(
                      e instanceof ApiError && e.code === "orders.already_booked"
                        ? "tiffin.already"
                        : "onboarding.error",
                    ),
                  ),
              },
            )
          }
        >
          {t("tiffin.cta", { n: qty, total: formatPaise(total) })}
        </Button>
      </div>
    </div>
  );
}
