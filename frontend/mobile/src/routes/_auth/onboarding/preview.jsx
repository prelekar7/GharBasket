import {
  Button,
  Chip,
  ErrorState,
  formatPaise,
  MoneyText,
  Skeleton,
  ThaliWeek,
} from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { nextStep } from "@/features/onboarding/draft.js";
import { reasonText, shortDate, timeRange, weekdayOf } from "@/features/subscriptions/format.js";
import { useCurrentCycle } from "@/features/subscriptions/queries.js";

/** Here's your first week: the planned thali week, why, and the first box (design Hf13, M3). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/preview")({ component: Preview });

function Preview() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const { data: cycle, isPending, isError, refetch } = useCurrentCycle(lang);

  return (
    <OnboardingFrame
      footer={
        cycle && (
          <>
            <Button
              variant="primary"
              size="lg"
              block
              onPress={() => navigate({ to: nextStep("/onboarding/preview") })}
            >
              {t("onboarding.preview.cta")}
            </Button>
            <Button variant="quiet" block onPress={() => navigate({ to: "/box" })}>
              {t("onboarding.preview.edit")}
            </Button>
          </>
        )
      }
    >
      <div className="flex flex-col gap-1.5">
        <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
          {t("onboarding.preview.eyebrow")}
        </span>
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {t("onboarding.preview.title")}
        </h1>
        <p className="text-fg-secondary">{t("onboarding.preview.sub")}</p>
      </div>
      {isPending ? (
        <Skeleton lines={5} />
      ) : isError || !cycle ? (
        <ErrorState
          title={t("onboarding.error")}
          retryLabel={t("onboarding.retry")}
          onRetry={() => refetch()}
        />
      ) : (
        <>
          {/* Edge to edge on the right, so the week visibly scrolls on (Hf13). */}
          <div className="-mr-5">
            <ThaliWeek
              label={t("onboarding.preview.title")}
              labels={{ today: t("week.today"), skip: t("week.skip") }}
              days={cycle.meal_plan.days.map((d) => ({
                day: t(`days.short.d${weekdayOf(d.date)}`),
                date: shortDate(d.date, lang),
                meals: d.meals.map((m) => ({
                  slot: t(`week.slot.${m.slot}`),
                  name: m.name,
                  tiffin: m.tiffin,
                  skip: m.skip,
                })),
              }))}
            />
          </div>
          <section className="flex flex-col gap-2">
            <span className="text-sm leading-5 font-semibold">{t("onboarding.preview.why")}</span>
            <div className="flex flex-wrap gap-2">
              {cycle.highlights.map((hl) => (
                <Chip key={hl.code} kind="reason">
                  {reasonText(t, hl)}
                </Chip>
              ))}
            </div>
          </section>
          <section className="flex items-center justify-between gap-3 rounded-md bg-surface p-4">
            <div className="flex flex-col gap-0.5">
              <strong>{t("onboarding.preview.box", { count: cycle.lines.length })}</strong>
              <p className="text-sm leading-5 text-fg-secondary">
                {t("onboarding.preview.when", {
                  day: t(`days.long.d${weekdayOf(cycle.delivery_date)}`),
                  range: timeRange(cycle.delivery_window, lang),
                })}
                {cycle.budget_lock &&
                  ` · ${t("onboarding.preview.within", { budget: formatPaise(cycle.budget_lock.budget_paise) })}`}
              </p>
            </div>
            <MoneyText paise={cycle.total_paise} size="lg" />
          </section>
        </>
      )}
    </OnboardingFrame>
  );
}
