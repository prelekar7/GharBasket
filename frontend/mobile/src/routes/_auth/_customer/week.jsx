import {
  Button,
  Dialog,
  EmptyBasket,
  EmptyState,
  formatPaise,
  MoneyText,
  OrderTimeline,
  ProgressBar,
  Skeleton,
  StatusPill,
  ThaliWeek,
  toast,
} from "@gharbasket/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { PILL } from "@/features/subscriptions/cycle.js";
import { DaySheet } from "@/features/subscriptions/DaySheet.jsx";
import { showCycleError } from "@/features/subscriptions/errors.js";
import {
  clockTime,
  istClock,
  istDate,
  monthName,
  shortDate,
  timeRange,
  weekdayName,
  weekdayOf,
  weekRange,
} from "@/features/subscriptions/format.js";
import { PaymentFailed } from "@/features/subscriptions/PaymentFailed.jsx";
import {
  isEditable,
  useCurrentCycle,
  useCycleAction,
  useFailedBoxOrder,
} from "@/features/subscriptions/queries.js";
import { markFirstBoxApproved } from "@/lib/install.js";

/**
 * Your week: the thali, the box, Budget Lock, approve or skip before the cut-off (design HcWeek, M3).
 * Also the approved week (Core-W3) and the skipped week with undo (W4). Before the first plan the
 * API answers no_cycle, which is an empty state.
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
  const [confirmSkip, setConfirmSkip] = useState(false);
  const [openDay, setOpenDay] = useState(/** @type {number | null} */ (null));
  const client = useQueryClient();
  // A box the cut-off couldn't charge: from its order and the wallet, not from the cycle (M4).
  // After a top-up the order is re-read until the debit has gone through.
  const [repaying, setRepaying] = useState(false);
  const failedOrder = useFailedBoxOrder(c, { poll: repaying });
  const order = failedOrder.data;
  useEffect(() => {
    if (!repaying) return;
    if (failedOrder.isSuccess && order === null) {
      setRepaying(false);
      toast(t("week.paidToast"));
      client.invalidateQueries({ queryKey: ["cycle"] });
      return;
    }
    const giveUp = setTimeout(() => {
      setRepaying(false);
      toast(t("week.payingStill"));
    }, 30_000);
    return () => clearTimeout(giveUp);
  }, [repaying, failedOrder.isSuccess, order, client, t]);

  if (isPending || failedOrder.isLoading) return <Skeleton lines={6} />;
  if (isError) return <ScreenError onRetry={() => refetch()} />;
  if (!c)
    return (
      <div className="flex flex-col gap-4">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">{t("week.title")}</h1>
        <EmptyState
          illustration={<EmptyBasket />}
          title={t("week.noCycleTitle")}
          body={t("week.noCycleBody")}
        />
      </div>
    );

  const today = c.meal_plan.days.findIndex((d) => d.date === istDate());
  const cutoffWhen = `${t(`days.long.d${weekdayOf(istDate(c.cutoff_at))}`)} ${clockTime(istClock(c.cutoff_at), lang)}`;
  const deliveryDay = `${t(`days.short.d${weekdayOf(c.delivery_date)}`)} ${shortDate(c.delivery_date, lang)}`;
  // Edits, including skipping, stay open until the cut-off, even after approving (W3).
  const editable = isEditable(c) && !order;
  // The failed-payment week shows only the alert, the wallet and the two ways out (design HcPayFailed).
  const failed = !!order;

  /** Says why an action failed; Try again runs it once more. @param {unknown} e @param {() => void} [retry] */
  const onError = (e, retry) => showCycleError(t, e, { retry, refetch });
  const approveWeek = () =>
    approve.mutate(c, {
      onSuccess: () => {
        markFirstBoxApproved();
        toast(t("week.approvedToast", { day: deliveryDay }));
      },
      onError: (e) => onError(e, approveWeek),
    });
  const skipWeek = () =>
    skip.mutate(c, {
      onSuccess: () => toast(t("week.skippedToast")),
      onError: (e) => onError(e, skipWeek),
    });
  const unskipWeek = () =>
    unskip.mutate(c, {
      onSuccess: () => toast(t("week.unskippedToast")),
      onError: (e) => onError(e, unskipWeek),
    });

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
          <StatusPill status={failed ? "issue" : PILL[c.status]}>
            {t(`week.status.${failed ? "payment_failed" : c.status}`)}
          </StatusPill>
        </div>
      </header>
      {!failed && c.status !== "approved" && c.status !== "skipped" && (
        <p className="-mt-2 text-sm leading-[22px] text-fg-secondary">
          {t(`week.lead.${c.status}`, {
            when: cutoffWhen,
            day: t(`days.long.d${weekdayOf(c.delivery_date)}`),
          })}
        </p>
      )}
      {!failed && c.status === "approved" && (
        <section className="flex flex-col gap-1 rounded-md border-2 border-primary bg-primary-soft px-4 py-3">
          <strong>{t("week.approvedTitle")}</strong>
          <p className="text-sm leading-5">
            {t("week.approvedBody", { amount: formatPaise(c.total_paise), when: cutoffWhen })}
          </p>
        </section>
      )}
      {order && (
        <PaymentFailed
          cycle={c}
          order={order}
          repaying={repaying}
          onPaid={() => setRepaying(true)}
          onError={onError}
        />
      )}
      {failed ? null : c.status === "skipped" ? (
        <>
          <EmptyState
            illustration={<EmptyBasket />}
            title={t("week.skippedTitle")}
            body={t("week.skippedBody", {
              range: weekRange(c.week_start, lang),
              next: `${weekdayName(nextWeek(c.delivery_date), lang)} ${shortDate(nextWeek(c.delivery_date), lang)}`,
            })}
            action={t("week.undoSkip")}
            onAction={unskipWeek}
          />
          <p className="-mt-2 text-center text-sm text-fg-secondary">
            {t("week.pauseLead")}{" "}
            <button
              type="button"
              onClick={() => navigate({ to: "/household" })}
              className="font-semibold text-link underline outline-none focus-visible:shadow-focus"
            >
              {t("week.pauseLink")}
            </button>
          </p>
        </>
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
                name: m.name ?? t("week.day.tiffinSlot"),
                tiffin: m.tiffin,
              })),
            }))}
          />
          <section className="flex flex-col gap-1 rounded-md bg-surface px-4 py-3.5">
            <div className="flex items-baseline justify-between gap-3">
              <strong>{t("onboarding.preview.box", { count: c.lines.length })}</strong>
              <MoneyText paise={c.total_paise} size="lg" />
            </div>
            <p className="text-sm leading-[22px] text-fg-secondary">
              {`${deliveryDay}, ${timeRange(c.slot, lang)}`}
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
                    {
                      label: t("week.paidFromWallet"),
                      time: cutoffWhen,
                      note: t("week.atCutoff"),
                      state: "current",
                    },
                    { label: t("track.stage.packed"), state: "todo" },
                    {
                      label: c.captain
                        ? t("orders.stage.AT_SOCIETY", { captain: c.captain.name })
                        : t("week.atSociety"),
                      time: `${weekdayName(c.delivery_date, lang)} ${timeRange(c.slot, lang)}`,
                      state: "todo",
                    },
                    ...(c.captain
                      ? [
                          {
                            label: t("track.stage.door", { flat: c.captain.flat }),
                            state: /** @type {const} */ ("todo"),
                          },
                        ]
                      : []),
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
              label={t("week.lock", { month: monthName(`${c.budget_lock.month}-15`, lang) })}
              value={Math.min(c.budget_lock.spent_paise, c.budget_lock.budget_paise)}
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
              onPress={approveWeek}
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
        onConfirm={skipWeek}
      >
        {t("week.skipBody", { amount: formatPaise(c.total_paise) })}
      </Dialog>
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
