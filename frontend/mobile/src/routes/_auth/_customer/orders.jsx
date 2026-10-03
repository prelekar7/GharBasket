import {
  Button,
  EmptyState,
  ErrorState,
  formatPaise,
  MoneyText,
  OrderTimeline,
  RadioCards,
  Sheet,
  Skeleton,
  StatusPill,
  TiffinSteam,
  toast,
} from "@gharbasket/ui";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useOrders, useReportIssue } from "@/features/orders/queries.js";
import {
  clockTime,
  istClock,
  shortDate,
  todayIst,
  weekdayName,
} from "@/features/subscriptions/format.js";

/** Orders: today's delivery with its timeline and what's coming up (design HcOrders, M4). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/orders")({ component: Orders });

/** Statuses after the cut-off and before the door: the order is being handled now. */
const ACTIVE = new Set([
  "LOCKED",
  "PICKING",
  "PACKED",
  "DISPATCHED",
  "AT_SOCIETY",
  "UNDELIVERED",
  "REDELIVERY",
  "ASSIGNED",
  "PREPARING",
  "READY",
  "PICKED_UP",
]);
/** @type {Record<string, "preparing" | "out" | "delivered" | "issue">} */
const PILL = {
  LOCKED: "preparing",
  PICKING: "preparing",
  PACKED: "preparing",
  DISPATCHED: "out",
  AT_SOCIETY: "out",
  DELIVERED: "delivered",
  UNDELIVERED: "issue",
  REDELIVERY: "out",
};
/** Box timeline stages; a status in between counts as the stage before it. */
const STAGES = ["APPROVED", "LOCKED", "PACKED", "AT_SOCIETY", "DELIVERED"];
const STAGE_OF = /** @type {Record<string, string>} */ ({
  PICKING: "LOCKED",
  DISPATCHED: "PACKED",
  UNDELIVERED: "AT_SOCIETY",
  REDELIVERY: "AT_SOCIETY",
});
const ISSUES = ["missing", "damaged", "late", "other"];

function Orders() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const { data, isPending, isError, refetch } = useOrders(lang);
  const [reporting, setReporting] = useState(false);
  const [issue, setIssue] = useState("missing");

  const today = todayIst();
  const day = (/** @type {string} */ iso) => `${weekdayName(iso, lang)} ${shortDate(iso, lang)}`;
  const header = (
    <header className="flex flex-col">
      <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
        {t("orders.today", { day: day(today) })}
      </span>
      <h1 className="font-display text-[28px] leading-[34px] font-semibold">{t("orders.title")}</h1>
    </header>
  );

  const current = data?.find(
    (o) => ACTIVE.has(o.status) || (o.status === "DELIVERED" && o.delivery_date === today),
  );
  const report = useReportIssue(current?.id);

  if (isPending)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <Skeleton lines={8} />
      </div>
    );
  if (isError || !data)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <ErrorState
          title={t("onboarding.error")}
          retryLabel={t("onboarding.retry")}
          onRetry={() => refetch()}
        />
      </div>
    );
  if (data.length === 0)
    return (
      <div className="flex flex-col gap-4">
        {header}
        <EmptyState
          illustration={<TiffinSteam />}
          title={t("orders.emptyTitle")}
          body={t("orders.emptyBody")}
        />
      </div>
    );

  const upcoming = data.filter((o) => o !== current && o.delivery_date >= today);

  /** When a stage happened: the time on the delivery day, else the weekday and time. */
  const at = (/** @type {string} */ iso, /** @type {string} */ deliveryDate) => {
    const time = clockTime(istClock(iso), lang);
    return iso.slice(0, 10) === deliveryDate ? time : `${weekdayName(iso, lang)} ${time}`;
  };
  const steps = current
    ? (() => {
        const reached = STAGES.indexOf(STAGE_OF[current.status] ?? current.status);
        const issueNow = current.status === "UNDELIVERED";
        return STAGES.map((stage, i) => {
          const event = current.events.find(
            (e) => (STAGE_OF[e.to_status] ?? e.to_status) === stage,
          );
          const done = i < reached || (i === reached && stage === "DELIVERED");
          return {
            label: t(`orders.stage.${stage}`, {
              hub: current.hub?.name ?? "",
              captain: current.captain?.name ?? "",
            }),
            time: event
              ? at(event.at, current.delivery_date)
              : stage === "DELIVERED" && current.eta_by
                ? t("orders.by", { time: clockTime(current.eta_by, lang) })
                : undefined,
            state: /** @type {"done" | "current" | "issue" | "todo"} */ (
              done ? "done" : i === reached ? (issueNow ? "issue" : "current") : "todo"
            ),
          };
        });
      })()
    : [];

  return (
    <div className="flex flex-col gap-3">
      {header}
      {current ? (
        <section className="mt-1 flex flex-col gap-3 rounded-md bg-surface px-4 py-3.5">
          <div className="flex items-center justify-between gap-2">
            <strong>
              {current.kind === "tiffin"
                ? t("orders.tiffinTitle", { dish: current.dish?.name ?? "" })
                : t("orders.box", { n: current.items_count ?? 0 })}
            </strong>
            <StatusPill status={PILL[current.status] ?? "preparing"}>
              {t(`orders.status.${current.status}`, { defaultValue: t("orders.status.LOCKED") })}
            </StatusPill>
          </div>
          <p className="-mt-2 text-sm leading-5 text-fg-secondary">
            {t(current.paid_from === "upi_autopay" ? "orders.paidUpi" : "orders.paidWallet", {
              amount: formatPaise(current.total_paise),
              flat: current.address.flat,
              society: current.address.society,
            })}
          </p>
          <figure className="m-0 h-[88px] overflow-hidden rounded-sm border border-line bg-bg">
            <MapSketch label={t("orders.mapLabel", { society: current.address.society })} />
          </figure>
          <OrderTimeline
            steps={steps}
            label={t("orders.box", { n: current.items_count ?? 0 })}
            labels={{
              done: t("orders.tl.done"),
              current: t("orders.tl.current"),
              issue: t("orders.tl.issue"),
              todo: t("orders.tl.todo"),
            }}
          />
          <div>
            <Button variant="secondary" onPress={() => setReporting(true)}>
              {t("orders.problem")}
            </Button>
          </div>
        </section>
      ) : (
        <EmptyState
          illustration={<TiffinSteam className="h-auto w-28" />}
          title={t("orders.nothingTitle")}
          body={t("orders.nothingBody")}
        />
      )}
      {upcoming.length > 0 && (
        <section className="flex flex-col">
          <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
            {t("orders.next")}
          </span>
          <ul>
            {upcoming.map((o) => (
              <li
                key={o.id}
                className="flex items-baseline justify-between gap-3 border-b border-line py-2.5 text-sm leading-5"
              >
                <span>
                  {o.kind === "tiffin"
                    ? t("orders.tiffin", {
                        day: day(o.delivery_date),
                        cook: o.cook?.name ?? "",
                        dish: o.dish?.name ?? "",
                      })
                    : o.plan_ready_on
                      ? t("orders.nextBox", {
                          day: day(o.delivery_date),
                          ready: day(o.plan_ready_on),
                        })
                      : t("orders.boxOn", { day: day(o.delivery_date) })}
                </span>
                {o.status === "AWAITING_APPROVAL" ? (
                  <span className="whitespace-nowrap text-fg-secondary">
                    {t("orders.toApprove")}
                  </span>
                ) : (
                  <MoneyText paise={o.total_paise} />
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      <Sheet
        open={reporting}
        onOpenChange={setReporting}
        title={t("orders.problem")}
        description={t("orders.problemLead")}
        footer={
          <Button
            variant="primary"
            size="lg"
            block
            isPending={report.isPending}
            onPress={() =>
              report.mutate(issue, {
                onSuccess: () => {
                  setReporting(false);
                  toast(t("orders.problemSent"));
                },
                onError: () => toast(t("orders.problemFailed")),
              })
            }
          >
            {t("orders.problemSend")}
          </Button>
        }
      >
        <RadioCards
          label={t("orders.problem")}
          value={issue}
          onChange={setIssue}
          options={ISSUES.map((value) => ({ value, title: t(`orders.issues.${value}`) }))}
        />
      </Sheet>
    </div>
  );
}

/** A sketch of the last stretch: two roads, your building, the van (no live map in v1). @param {{ label: string }} props */
function MapSketch({ label }) {
  return (
    <svg viewBox="0 0 318 88" role="img" aria-label={label} className="block h-full w-full">
      <path
        d="M-10 64C70 52 150 60 330 30"
        className="fill-none stroke-line-strong opacity-45"
        strokeWidth={9}
        strokeLinecap="round"
      />
      <path
        d="M210 -10C214 30 220 60 206 100"
        className="fill-none stroke-line-strong opacity-45"
        strokeWidth={9}
        strokeLinecap="round"
      />
      <rect x="236" y="8" width="54" height="34" rx="5" className="fill-line" />
      <rect x="40" y="10" width="60" height="30" rx="5" className="fill-line" />
      <rect x="242" y="14" width="42" height="22" rx="4" className="fill-accent" />
      <circle cx="206" cy="46" r="12" className="fill-primary" />
      <path d="M199 43h9v6h-9zM208 45h3l2 2v2h-5z" className="fill-on-primary" />
    </svg>
  );
}
