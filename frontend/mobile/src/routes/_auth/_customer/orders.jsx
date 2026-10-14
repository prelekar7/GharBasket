import {
  Button,
  EmptyState,
  formatPaise,
  MoneyText,
  OrderTimeline,
  Skeleton,
  StatusPill,
  TiffinSteam,
} from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { hhmm, istDate } from "@/features/orders/format.js";
import { ProblemSheet } from "@/features/orders/ProblemSheet.jsx";
import {
  useDeliveryPlace,
  useOrder,
  useOrders,
  useRating,
  useTiffinInfo,
} from "@/features/orders/queries.js";
import {
  ACTIVE,
  FINISHED,
  PILL,
  STAGE_KEY,
  STAGE_OF,
  STAGES,
  statusKey,
  TIFFIN_STAGE_KEY,
  TIFFIN_STAGE_OF,
  TIFFIN_STAGES,
  TRACKABLE,
} from "@/features/orders/status.js";
import {
  clockTime,
  istClock,
  shortDate,
  todayIst,
  weekdayName,
} from "@/features/subscriptions/format.js";
import { useCurrentCycle } from "@/features/subscriptions/queries.js";

/**
 * Orders: today's delivery with its timeline, live tracking, rating it once delivered, and what's
 * coming up (designs HcOrders, HcTracking, HcRate; M4). Orders exist from the moment a week is
 * approved; the week still waiting for approval is M3's cycle, shown as the last "coming up" row.
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/orders")({ component: Orders });

/** Today's box first, then a tiffin the same day. @param {import("@/features/orders/queries.js").OrderSummary} a @param {import("@/features/orders/queries.js").OrderSummary} b */
const soonest = (a, b) =>
  a.delivery_date.localeCompare(b.delivery_date) ||
  Number(a.kind !== "box") - Number(b.kind !== "box");

function Orders() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const { data, isPending, isError, refetch } = useOrders(lang);
  const cycle = useCurrentCycle(lang);
  const place = useDeliveryPlace();
  const navigate = useNavigate();
  const [reporting, setReporting] = useState(false);

  const today = todayIst();
  // A delivery stays on top until the day after, so it can still be rated or reported.
  const yesterday = new Date(Date.parse(`${today}T12:00:00Z`) - 86_400_000)
    .toISOString()
    .slice(0, 10);
  const day = (/** @type {string} */ iso) => `${weekdayName(iso, lang)} ${shortDate(iso, lang)}`;
  const header = (
    <header className="flex flex-col">
      <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
        {t("orders.today", { day: day(today) })}
      </span>
      <h1 className="font-display text-[28px] leading-[34px] font-semibold">{t("orders.title")}</h1>
    </header>
  );

  // The API lists the newest delivery first; the screen wants the soonest.
  const orders = [...(data ?? [])].sort(soonest);
  // An order is "now" only from its delivery day: a box locked tonight for tomorrow is still
  // "coming up" until the morning it is delivered.
  const current = orders.find(
    (o) =>
      (ACTIVE.has(o.status) && o.delivery_date <= today) ||
      (o.status === "delivered" && o.delivery_date >= yesterday),
  );
  // The list has no lines or timeline: the one order on show is read in full.
  const detail = useOrder(current?.id, lang);
  const tiffin = useTiffinInfo(current?.kind === "tiffin" ? current.id : undefined, lang);
  const rating = useRating(current?.status === "delivered" ? current.id : undefined);

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
        <ScreenError onRetry={() => refetch()} />
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

  /** The week still to approve (M3): not an order yet. */
  const toApprove =
    /** @type {{ status: string, delivery_date: string, generated_at?: string | null } | undefined} */ (
      cycle.data
    );
  /** @type {({ id: string, date: string } & ({ order: import("@/features/orders/queries.js").OrderSummary } | { readyOn: string | null }))[]} */
  const upcoming = orders
    .filter((o) => o !== current && !FINISHED.has(o.status) && o.delivery_date >= today)
    .map((order) => ({ id: order.id, date: order.delivery_date, order }));
  if (toApprove?.status === "awaiting_approval")
    upcoming.push({
      id: "to-approve",
      date: toApprove.delivery_date,
      readyOn: toApprove.generated_at ? istDate(toApprove.generated_at) : null,
    });
  upcoming.sort((a, b) => a.date.localeCompare(b.date));

  /** When a stage happened: the time on the delivery day, else the weekday and time. */
  const at = (/** @type {string} */ iso, /** @type {string} */ deliveryDate) => {
    const time = clockTime(istClock(iso), lang);
    return istDate(iso) === deliveryDate ? time : `${weekdayName(istDate(iso), lang)} ${time}`;
  };
  const steps = current
    ? (() => {
        const tiffinOrder = current.kind === "tiffin";
        /** @type {readonly string[]} */
        const stages = tiffinOrder ? TIFFIN_STAGES : STAGES;
        /** @type {Record<string, string | undefined>} */
        const stageOf = tiffinOrder ? TIFFIN_STAGE_OF : STAGE_OF;
        const reached = stages.indexOf(stageOf[current.status] ?? current.status);
        const issueNow = current.status === "undelivered";
        /** @param {string} stage */
        const label = (stage) => {
          if (tiffinOrder)
            return t(
              `orders.stage.${TIFFIN_STAGE_KEY[/** @type {keyof typeof TIFFIN_STAGE_KEY} */ (stage)]}`,
              {
                cook: tiffin.data?.cook.name ?? "",
              },
            );
          if (stage === "packed") return t("track.stage.packed");
          if (stage === "at_society" && !place.captain) return t("orders.withCaptain");
          return t(`orders.stage.${STAGE_KEY[/** @type {keyof typeof STAGE_KEY} */ (stage)]}`, {
            captain: place.captain ?? "",
          });
        };
        return stages.map((stage, i) => {
          const event = detail.data?.timeline.find(
            (e) => (stageOf[e.status] ?? e.status) === stage,
          );
          const done = i < reached || (i === reached && stage === "delivered");
          return {
            label: label(stage),
            time: event
              ? at(event.at, current.delivery_date)
              : stage === "delivered"
                ? t("orders.by", { time: clockTime(hhmm(current.slot.end), lang) })
                : undefined,
            state: /** @type {"done" | "current" | "issue" | "todo"} */ (
              done ? "done" : i === reached ? (issueNow ? "issue" : "current") : "todo"
            ),
          };
        });
      })()
    : [];
  const where = [place.flat, place.society].filter(Boolean).join(", ");
  const boxTitle = detail.data
    ? t("orders.box", { n: detail.data.lines.length })
    : t("orders.boxPlain");

  return (
    <div className="flex flex-col gap-3">
      {header}
      {current ? (
        <section className="mt-1 flex flex-col gap-3 rounded-md bg-surface px-4 py-3.5">
          <div className="flex items-center justify-between gap-2">
            <strong>
              {current.kind === "tiffin"
                ? tiffin.data
                  ? t("orders.tiffinTitle", { dish: tiffin.data.dish.name })
                  : t("tiffin.title")
                : boxTitle}
            </strong>
            <StatusPill status={PILL[current.status]}>{t(statusKey(current))}</StatusPill>
          </div>
          <p className="-mt-2 text-sm leading-5 text-fg-secondary">
            {t("orders.paid", { amount: formatPaise(current.total_paise) })}
            {where && ` · ${where}`}
          </p>
          <figure className="m-0 h-[88px] overflow-hidden rounded-sm border border-line bg-bg">
            <MapSketch
              label={t("orders.mapLabel", { society: place.society ?? t("orders.yourSociety") })}
            />
          </figure>
          <OrderTimeline
            steps={steps}
            label={boxTitle}
            labels={{
              done: t("orders.tl.done"),
              current: t("orders.tl.current"),
              issue: t("orders.tl.issue"),
              todo: t("orders.tl.todo"),
            }}
          />
          {rating.data && (
            <p className="text-sm leading-5 text-fg-secondary">{t("orders.rated")}</p>
          )}
          <div className="flex flex-wrap gap-2">
            {current.status === "delivered" && rating.isSuccess && !rating.data && (
              <Button
                variant="primary"
                onPress={() =>
                  navigate({ to: "/orders/$orderId/rate", params: { orderId: current.id } })
                }
              >
                {t("orders.rate")}
              </Button>
            )}
            {current.kind === "box" && TRACKABLE.has(current.status) && (
              <Button
                variant="primary"
                onPress={() =>
                  navigate({ to: "/orders/$orderId/track", params: { orderId: current.id } })
                }
              >
                {t("orders.track")}
              </Button>
            )}
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
            {upcoming.map((u) => (
              <li
                key={u.id}
                className="flex items-baseline justify-between gap-3 border-b border-line py-2.5 text-sm leading-5"
              >
                {"order" in u ? (
                  <OrderRow order={u.order} lang={lang} day={day(u.date)} />
                ) : (
                  <>
                    <span>
                      {u.readyOn
                        ? t("orders.nextBox", { day: day(u.date), ready: day(u.readyOn) })
                        : t("orders.boxOn", { day: day(u.date) })}
                    </span>
                    <span className="whitespace-nowrap text-fg-secondary">
                      {t("orders.toApprove")}
                    </span>
                  </>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      <ProblemSheet orderId={current?.id} open={reporting} onOpenChange={setReporting} />
    </div>
  );
}

/**
 * A "coming up" row for an order: what it is on the left, what it costs (or what is wrong) on the
 * right. @param {{ order: import("@/features/orders/queries.js").OrderSummary, lang: string, day: string }} props
 */
function OrderRow({ order, lang, day }) {
  const { t } = useTranslation();
  const tiffin = useTiffinInfo(order.kind === "tiffin" ? order.id : undefined, lang);
  return (
    <>
      <span>
        {order.kind === "tiffin"
          ? tiffin.data
            ? t("orders.tiffin", { day, cook: tiffin.data.cook.name, dish: tiffin.data.dish.name })
            : t("orders.tiffinOn", { day })
          : t("orders.boxOn", { day })}
      </span>
      {order.status === "payment_failed" ? (
        <span className="whitespace-nowrap text-fg-secondary">{t(statusKey(order))}</span>
      ) : (
        <MoneyText paise={order.total_paise} />
      )}
    </>
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
