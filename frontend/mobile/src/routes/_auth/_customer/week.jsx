import {
  Button,
  Dialog,
  ErrorState,
  formatPaise,
  MoneyText,
  ProgressBar,
  Skeleton,
  StatusPill,
  ThaliWeek,
  toast,
} from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  clockTime,
  monthName,
  shortDate,
  timeRange,
  weekdayOf,
  weekRange,
} from "@/features/subscriptions/format.js";
import { useCurrentCycle, useCycleAction } from "@/features/subscriptions/queries.js";
import { ApiError } from "@/lib/api.js";
import { markFirstBoxApproved } from "@/lib/install.js";

/** @type {Record<string, "planned" | "preparing" | "ready" | "out" | "delivered" | "issue">} */
const PILL = { ready: "preparing", approved: "ready", skipped: "planned", locked: "out" };

/** Your week: the thali, the box, Budget Lock, approve or skip before the cut-off (design HcWeek, M3). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/week")({ component: Week });

function Week() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const { data: c, isPending, isError, refetch } = useCurrentCycle(lang);
  const approve = useCycleAction("approve");
  const skip = useCycleAction("skip");
  const [confirmSkip, setConfirmSkip] = useState(false);

  if (isPending) return <Skeleton lines={6} />;
  if (isError || !c)
    return (
      <ErrorState
        title={t("onboarding.error")}
        retryLabel={t("onboarding.retry")}
        onRetry={() => refetch()}
      />
    );

  const today = c.meal_plan.days.findIndex(
    (d) => d.date === new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" }),
  );
  const cutoff = new Date(c.cutoff_at);
  const cutoffDate = c.cutoff_at.slice(0, 10);
  const cutoffWhen = `${t(`days.long.d${weekdayOf(cutoffDate)}`)} ${clockTime(cutoff.toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit" }), lang)}`;
  const deliveryDay = `${t(`days.short.d${weekdayOf(c.delivery_date)}`)} ${shortDate(c.delivery_date, lang)}`;
  const editable = c.status === "ready";

  /** @param {unknown} e */
  const onError = (e) => {
    if (e instanceof ApiError && e.code === "subscriptions.version_conflict") {
      toast(t("week.changed"));
      refetch();
    } else toast(t("onboarding.error"));
  };

  return (
    <div className="flex flex-col gap-4">
      <header className="flex items-start justify-between gap-2">
        <div className="flex flex-col">
          <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
            {weekRange(c.week_start, lang)}
          </span>
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">
            {t("week.title")}
          </h1>
        </div>
        <div className="mt-1.5">
          <StatusPill status={PILL[c.status]}>{t(`week.status.${c.status}`)}</StatusPill>
        </div>
      </header>
      <p className="-mt-2 text-sm leading-[22px] text-fg-secondary">
        {t(`week.lead.${c.status}`, {
          when: cutoffWhen,
          day: t(`days.long.d${weekdayOf(c.delivery_date)}`),
        })}
      </p>
      <ThaliWeek
        label={t("week.title")}
        today={today >= 0 ? today : undefined}
        labels={{ today: t("week.today"), skip: t("week.skip") }}
        days={c.meal_plan.days.map((d) => ({
          day: t(`days.short.d${weekdayOf(d.date)}`),
          date: shortDate(d.date, lang),
          meals: d.meals.map((m) => ({
            slot: t(`week.slot.${m.slot}`),
            name: m.name,
            tiffin: m.tiffin,
            skip: m.skip || c.status === "skipped",
          })),
        }))}
      />
      <section className="flex flex-col gap-1 rounded-md bg-surface px-4 py-3.5">
        <div className="flex items-baseline justify-between gap-3">
          <strong>{t("onboarding.preview.box", { count: c.lines.length })}</strong>
          <MoneyText paise={c.total_paise} size="lg" strike={c.status === "skipped"} />
        </div>
        <p className="text-sm leading-[22px] text-fg-secondary">
          {`${deliveryDay}, ${timeRange(c.delivery_window, lang)}`}
          {c.captain && ` · ${t("week.brings", { name: c.captain.name, flat: c.captain.flat })}`}
        </p>
        <div>
          <Button variant="quiet" className="-ml-4" onPress={() => navigate({ to: "/box" })}>
            {t("week.open")}
          </Button>
        </div>
      </section>
      {c.budget_lock && (
        <ProgressBar
          label={t("week.lock", { month: monthName(c.week_start, lang) })}
          value={c.budget_lock.spent_paise}
          max={c.budget_lock.budget_paise}
          tone="accent"
          valueLabel={t("week.lockValue", {
            spent: formatPaise(c.budget_lock.spent_paise),
            budget: formatPaise(c.budget_lock.budget_paise),
          })}
        />
      )}
      {editable && (
        <div className="flex flex-col gap-1">
          <Button
            variant="primary"
            size="lg"
            block
            isPending={approve.isPending}
            onPress={() =>
              approve.mutate(c, {
                onSuccess: () => {
                  markFirstBoxApproved();
                  toast(t("week.approvedToast", { day: deliveryDay }));
                },
                onError,
              })
            }
          >
            {t("week.approve")}
          </Button>
          <Button variant="quiet" block onPress={() => setConfirmSkip(true)}>
            {t("week.skipWeek")}
          </Button>
        </div>
      )}
      <Dialog
        isOpen={confirmSkip}
        onOpenChange={setConfirmSkip}
        title={t("week.skipTitle", { day: deliveryDay })}
        confirmLabel={t("week.skipConfirm")}
        cancelLabel={t("week.skipCancel")}
        tone="danger"
        onConfirm={() =>
          skip.mutate(c, { onSuccess: () => toast(t("week.skippedToast")), onError })
        }
      >
        {t("week.skipBody", { amount: formatPaise(c.total_paise) })}
      </Dialog>
    </div>
  );
}
