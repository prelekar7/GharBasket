import {
  Button,
  Dialog,
  EmptyBasket,
  EmptyState,
  ErrorState,
  formatPaise,
  MoneyText,
  OrderTimeline,
  ProgressBar,
  Skeleton,
  StatusPill,
  ThaliWeek,
  toast,
} from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useTopUp } from "@/features/billing/useTopUp.jsx";
import { DaySheet } from "@/features/subscriptions/DaySheet.jsx";
import {
  clockTime,
  istClock,
  monthName,
  shortDate,
  timeRange,
  weekdayName,
  weekdayOf,
  weekRange,
} from "@/features/subscriptions/format.js";
import { useCurrentCycle, useCycleAction } from "@/features/subscriptions/queries.js";
import { ApiError } from "@/lib/api.js";
import { markFirstBoxApproved } from "@/lib/install.js";

/** @type {Record<string, "planned" | "preparing" | "ready" | "out" | "delivered" | "issue">} */
const PILL = {
  ready: "preparing",
  approved: "ready",
  skipped: "planned",
  locked: "out",
  payment_failed: "issue",
};

/**
 * Your week: the thali, the box, Budget Lock, approve or skip before the cut-off (design HcWeek, M3).
 * Also the approved week (Core-W3), the skipped week with undo (W4) and a short wallet at the
 * cut-off (D4).
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/week")({ component: Week });

function Week() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const { data: c, isPending, isError, refetch } = useCurrentCycle(lang);
  const approve = useCycleAction("approve");
  const skip = useCycleAction("skip");
  const unskip = useCycleAction("unskip");
  const pay = useCycleAction("pay");
  const [confirmSkip, setConfirmSkip] = useState(false);
  const [openDay, setOpenDay] = useState(/** @type {number | null} */ (null));
  // After a top-up in the grace window, retry the box payment straight away.
  const topUp = useTopUp({
    onPaid: () => {
      if (c) pay.mutate(c, { onSuccess: () => toast(t("week.paidToast")), onError });
    },
  });

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
  // Edits, including skipping, stay open until the cut-off, even after approving (W3).
  const editable = c.status === "ready" || c.status === "approved";
  const due = c.payment_due;

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
      {(c.status === "ready" || c.status === "locked") && (
        <p className="-mt-2 text-sm leading-[22px] text-fg-secondary">
          {t(`week.lead.${c.status}`, {
            when: cutoffWhen,
            day: t(`days.long.d${weekdayOf(c.delivery_date)}`),
          })}
        </p>
      )}
      {c.status === "approved" && (
        <section className="flex flex-col gap-1 rounded-md border-2 border-primary bg-primary-soft px-4 py-3">
          <strong>{t("week.approvedTitle")}</strong>
          <p className="text-sm leading-5">
            {t("week.approvedBody", { amount: formatPaise(c.total_paise), when: cutoffWhen })}
          </p>
        </section>
      )}
      {c.status === "payment_failed" && due && (
        <section
          role="alert"
          className="flex flex-col gap-2 rounded-md border-2 border-danger bg-bg px-4 py-3"
        >
          <strong>{t("week.failedTitle")}</strong>
          <p className="text-sm leading-5">
            {t("week.failedBody", {
              wallet: formatPaise(due.wallet_paise),
              total: formatPaise(c.total_paise),
              short: formatPaise(due.shortfall_paise),
              time: clockTime(istClock(due.due_at), lang),
              day: t(`days.long.d${weekdayOf(c.delivery_date)}`),
            })}
          </p>
          {topUp.error && (
            <p role="alert" className="text-sm font-semibold text-danger">
              {topUp.error}
            </p>
          )}
          <Button
            variant="primary"
            block
            isPending={topUp.busy || pay.isPending}
            onPress={() => topUp.start(due.shortfall_paise)}
          >
            {t("week.topUpShort", { short: formatPaise(due.shortfall_paise) })}
          </Button>
          <Button variant="quiet" block onPress={() => setConfirmSkip(true)}>
            {t("week.skipInstead")}
          </Button>
        </section>
      )}
      {c.status === "skipped" ? (
        <EmptyState
          illustration={<EmptyBasket />}
          title={t("week.skippedTitle")}
          body={t("week.skippedBody", {
            range: weekRange(c.week_start, lang),
            next: `${weekdayName(nextWeek(c.delivery_date), lang)} ${shortDate(nextWeek(c.delivery_date), lang)}`,
          })}
          action={t("week.undoSkip")}
          onAction={() =>
            unskip.mutate(c, { onSuccess: () => toast(t("week.unskippedToast")), onError })
          }
        />
      ) : (
        <>
          <ThaliWeek
            label={t("week.title")}
            today={today >= 0 ? today : undefined}
            labels={{ today: t("week.today"), skip: t("week.skip"), open: t("week.day.open") }}
            onDayPress={setOpenDay}
            days={c.meal_plan.days.map((d) => ({
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
          <section className="flex flex-col gap-1 rounded-md bg-surface px-4 py-3.5">
            <div className="flex items-baseline justify-between gap-3">
              <strong>{t("onboarding.preview.box", { count: c.lines.length })}</strong>
              <MoneyText paise={c.total_paise} size="lg" />
            </div>
            <p className="text-sm leading-[22px] text-fg-secondary">
              {`${deliveryDay}, ${timeRange(c.delivery_window, lang)}`}
              {c.captain &&
                ` · ${t("week.brings", { name: c.captain.name, flat: c.captain.flat })}`}
            </p>
            {c.status === "approved" && (
              <div className="mt-2">
                <OrderTimeline
                  label={t("week.boxTimeline", {
                    day: t(`days.long.d${weekdayOf(c.delivery_date)}`),
                  })}
                  labels={{
                    done: t("orders.tl.done"),
                    current: t("orders.tl.current"),
                    issue: t("orders.tl.issue"),
                    todo: t("orders.tl.todo"),
                  }}
                  steps={[
                    {
                      label: t("orders.stage.APPROVED"),
                      time: c.approved_at
                        ? `${weekdayName(c.approved_at, lang)} ${clockTime(istClock(c.approved_at), lang)}`
                        : undefined,
                      state: "done",
                    },
                    { label: t("orders.stage.LOCKED"), time: cutoffWhen, state: "todo" },
                    { label: t("orders.stage.PACKED", { hub: c.hub.name }), state: "todo" },
                    {
                      label: t("orders.stage.AT_SOCIETY", { captain: c.captain?.name ?? "" }),
                      time: `${weekdayName(c.delivery_date, lang)} ${timeRange(c.delivery_window, lang)}`,
                      state: "todo",
                    },
                  ]}
                />
              </div>
            )}
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
        </>
      )}
      {editable && (
        <div className="flex flex-col gap-1">
          {c.status === "approved" ? (
            <Button variant="secondary" size="lg" block onPress={() => navigate({ to: "/box" })}>
              {t("week.editBox")}
            </Button>
          ) : (
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
          )}
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
      {topUp.dialog}
      <DaySheet
        cycle={c}
        index={openDay}
        onClose={() => setOpenDay(null)}
        editable={editable}
        onError={onError}
      />
    </div>
  );
}

/** The same weekday a week later. @param {string} iso */
function nextWeek(iso) {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 7);
  return d.toISOString().slice(0, 10);
}
