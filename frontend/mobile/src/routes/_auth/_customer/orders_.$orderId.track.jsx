import { Button, IconButton, OrderTimeline, Skeleton, StatusPill } from "@gharbasket/ui";
import { ArrowLeftIcon, TruckIcon } from "@phosphor-icons/react";
import { createFileRoute, useRouter } from "@tanstack/react-router";
import { lazy, Suspense, useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useDelivery, useLiveRoute } from "@/features/logistics/tracking.js";
import { hhmm } from "@/features/orders/format.js";
import { ProblemSheet } from "@/features/orders/ProblemSheet.jsx";
import { useDeliveryPlace, useOrder } from "@/features/orders/queries.js";
import { legOf, PILL, statusKey } from "@/features/orders/status.js";
import { clockTime, istClock, shortDate, weekdayName } from "@/features/subscriptions/format.js";

const TrackMap = lazy(() => import("@/components/TrackMap.jsx"));

/**
 * Live tracking of today's box: the van on a map, when it reaches your society, the last metres
 * (design HcTracking; M4, M7). The order gives the steps and times; the delivery card (§3.5) the
 * ETA and the captain, and the van's stream (§3.3) its position and how many stops come before
 * yours. The customer never sees the rider. The map and the stops drop out while there is no stream.
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/orders_/$orderId/track")({
  component: Track,
});

function Track() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const router = useRouter();
  const { orderId } = Route.useParams();
  const order = useOrder(orderId, lang);
  const o = order.data;
  const delivery = useDelivery(orderId).data;
  const live = useLiveRoute(o?.status === "dispatched" ? delivery?.tracking?.route_id : undefined);
  const place = useDeliveryPlace();
  const [reporting, setReporting] = useState(false);
  const captain = delivery?.captain?.display_name ?? place.captain;

  const back = (
    <IconButton label={t("onboarding.back")} variant="quiet" onPress={() => router.history.back()}>
      <ArrowLeftIcon size={24} />
    </IconButton>
  );
  if (order.isError || (!o && !order.isPending))
    return (
      <div className="flex flex-col gap-4">
        <div className="-ml-3">{back}</div>
        <ScreenError onRetry={() => order.refetch()} />
      </div>
    );
  if (!o)
    return (
      <div className="flex flex-col gap-4">
        <div className="-ml-3">{back}</div>
        <Skeleton className="h-[220px] w-full" />
        <Skeleton lines={6} />
      </div>
    );

  const time = (/** @type {string} */ clock) => clockTime(clock, lang);
  const at = (/** @type {import("@/features/orders/queries.js").OrderStatus} */ status) => {
    const e = o.timeline.find((x) => x.status === status);
    return e ? time(istClock(e.at)) : undefined;
  };
  const leg = legOf(o.status);
  const arrived = leg >= 2;
  const delivered = leg === 3;
  const society = place.society ?? t("orders.yourSociety");
  // The slot's end is when the box is at the door by; the van's own ETA, when known, is for the gate.
  const doorBy = time(hhmm(o.slot.end));
  const gateBy =
    (live.stop?.eta_now ?? delivery?.eta)
      ? time(live.stop?.eta_now ?? delivery?.eta ?? "")
      : doorBy;
  /** @type {{ label: string, time?: string, state: "done" | "current" | "todo" }[]} */
  const steps = [
    { label: t("track.stage.packed"), time: at("packed"), state: leg >= 1 ? "done" : "current" },
    { label: t("track.stage.left"), time: at("dispatched"), state: leg >= 1 ? "done" : "todo" },
    {
      label: t("track.stage.society", { society }),
      time: arrived ? at("at_society") : t("track.about", { time: gateBy }),
      state: arrived ? "done" : leg === 1 ? "current" : "todo",
    },
    {
      label: captain ? t("track.stage.captain", { captain }) : t("track.stage.captainNone"),
      state: delivered ? "done" : arrived ? "current" : "todo",
    },
    {
      label: place.flat ? t("track.stage.door", { flat: place.flat }) : t("track.stage.doorNone"),
      time: delivered ? at("delivered") : t("track.by", { time: doorBy }),
      state: delivered ? "done" : "todo",
    },
  ];
  const title = t("track.title", {
    day: `${weekdayName(o.delivery_date, lang)} ${shortDate(o.delivery_date, lang)}`,
  });

  return (
    <div className="flex min-h-[calc(100dvh-180px)] flex-col gap-3.5">
      <header className="-ml-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          {back}
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">{title}</h1>
        </div>
        <div className="shrink-0 whitespace-nowrap">
          <StatusPill status={PILL[o.status]}>{t(statusKey(o))}</StatusPill>
        </div>
      </header>
      {live.position && (
        <figure className="m-0">
          <Suspense fallback={<Skeleton className="h-[220px] w-full" />}>
            <TrackMap
              van={live.position}
              labels={{ van: t("track.vanLabel"), society }}
              fallback={
                <div className="grid h-[220px] place-items-center rounded-md border border-dashed border-line-strong bg-surface p-4 text-center text-sm leading-5 text-fg-secondary">
                  {arrived ? t("track.noMapThere", { society }) : t("track.noMap", { society })}
                </div>
              }
            />
          </Suspense>
          {live.lastSeenMin !== null && (
            <figcaption className="text-xs leading-4 text-fg-secondary">
              {t("track.lastSeen", { count: live.lastSeenMin })}
            </figcaption>
          )}
        </figure>
      )}
      <section className="flex items-center gap-3 rounded-md bg-surface px-3.5 py-3">
        <TruckIcon aria-hidden size={28} className="shrink-0 text-role-rider" />
        <div className="flex flex-col">
          <strong>
            {delivered
              ? t("track.delivered", { time: at("delivered") ?? "" })
              : arrived
                ? t("track.arrived", { society, time: at("at_society") ?? "" })
                : t("track.eta", { society, time: gateBy })}
          </strong>
          {!delivered && (
            <span className="text-sm leading-5 text-fg-secondary">
              {arrived
                ? captain
                  ? t("track.captain", { captain, time: doorBy })
                  : t("track.captainNone", { time: doorBy })
                : live.stop?.stops_before !== undefined &&
                  t("track.stopsBefore", { count: live.stop.stops_before })}
            </span>
          )}
        </div>
      </section>
      <OrderTimeline
        steps={steps}
        label={title}
        labels={{
          done: t("orders.tl.done"),
          current: t("orders.tl.current"),
          issue: t("orders.tl.issue"),
          todo: t("orders.tl.todo"),
        }}
      />
      <div className="mt-auto">
        <Button variant="quiet" block onPress={() => setReporting(true)}>
          {t("orders.problem")}
        </Button>
      </div>
      <ProblemSheet orderId={o.id} open={reporting} onOpenChange={setReporting} />
    </div>
  );
}
