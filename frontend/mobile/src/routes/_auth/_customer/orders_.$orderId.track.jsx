import { Button, IconButton, OrderTimeline, Skeleton, StatusPill } from "@gharbasket/ui";
import { ArrowLeftIcon, TruckIcon } from "@phosphor-icons/react";
import { createFileRoute, useRouter } from "@tanstack/react-router";
import { lazy, Suspense, useState } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "@/components/ScreenError.jsx";
import { ProblemSheet } from "@/features/orders/ProblemSheet.jsx";
import { useOrder, useTracking } from "@/features/orders/queries.js";
import { clockTime, istClock, shortDate, weekdayName } from "@/features/subscriptions/format.js";

const TrackMap = lazy(() => import("@/components/TrackMap.jsx"));

/** Live tracking of today's box: the van on a map, when it reaches your society, the last metres (design HcTracking; M4, M7). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/orders_/$orderId/track")({
  component: Track,
});

/** Straight-line distance in km, for the words under a map that cannot load. */
const km = (
  /** @type {{ lat: number, lng: number }} */ a,
  /** @type {{ lat: number, lng: number }} */ b,
) => {
  const rad = Math.PI / 180;
  const h =
    Math.sin(((b.lat - a.lat) * rad) / 2) ** 2 +
    Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(((b.lng - a.lng) * rad) / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
};

function Track() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const router = useRouter();
  const { orderId } = Route.useParams();
  const order = useOrder(orderId, lang);
  const live = useTracking(orderId, lang);
  const [reporting, setReporting] = useState(false);
  const o = order.data;
  const v = live.data;

  const back = (
    <IconButton label={t("onboarding.back")} variant="quiet" onPress={() => router.history.back()}>
      <ArrowLeftIcon size={24} />
    </IconButton>
  );
  if (order.isError || live.isError || ((!o || !v) && !order.isPending && !live.isPending))
    return (
      <div className="flex flex-col gap-4">
        <div className="-ml-3">{back}</div>
        <ScreenError
          onRetry={() => {
            order.refetch();
            live.refetch();
          }}
        />
      </div>
    );
  if (!o || !v)
    return (
      <div className="flex flex-col gap-4">
        <div className="-ml-3">{back}</div>
        <Skeleton className="h-[220px] w-full" />
        <Skeleton lines={6} />
      </div>
    );

  const time = (/** @type {string} */ hhmm) => clockTime(hhmm, lang);
  const at = (/** @type {string} */ status) => {
    const e = o.events.find((x) => x.to_status === status);
    return e ? time(istClock(e.at)) : undefined;
  };
  const arrived = o.status !== "DISPATCHED";
  const delivered = o.status === "DELIVERED";
  const captain = o.captain?.name ?? "";
  /** @type {{ label: string, time?: string, state: "done" | "current" | "todo" }[]} */
  const steps = [
    { label: t("track.stage.packed"), time: at("PACKED"), state: "done" },
    { label: t("track.stage.left"), time: at("DISPATCHED"), state: "done" },
    {
      label: t("track.stage.society", { society: v.society.name }),
      time: arrived ? at("AT_SOCIETY") : t("track.about", { time: time(v.eta_at) }),
      state: arrived ? "done" : "current",
    },
    {
      label: t("track.stage.captain", { captain }),
      state: delivered ? "done" : arrived ? "current" : "todo",
    },
    {
      label: t("track.stage.door", { flat: o.address.flat }),
      time: delivered
        ? at("DELIVERED")
        : o.eta_by
          ? t("track.by", { time: time(o.eta_by) })
          : undefined,
      state: delivered ? "done" : "todo",
    },
  ];
  const away = km(v.van, v.society);
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
          <StatusPill status={delivered ? "delivered" : "out"}>
            {t(`orders.status.${o.status}`)}
          </StatusPill>
        </div>
      </header>
      <figure className="m-0">
        <Suspense fallback={<Skeleton className="h-[220px] w-full" />}>
          <TrackMap
            van={v.van}
            society={v.society}
            labels={{
              van: t("track.vanLabel", { rider: v.rider.name }),
              society: v.society.name,
            }}
            fallback={
              <div className="grid h-[220px] place-items-center rounded-md border border-dashed border-line-strong bg-surface p-4 text-center text-sm leading-5 text-fg-secondary">
                {arrived
                  ? t("track.noMapThere", { society: v.society.name })
                  : t("track.noMap", {
                      km: away.toLocaleString(lang, { maximumFractionDigits: 1 }),
                      society: v.society.name,
                    })}
              </div>
            }
          />
        </Suspense>
      </figure>
      <section className="flex items-center gap-3 rounded-md bg-surface px-3.5 py-3">
        <TruckIcon aria-hidden size={28} className="shrink-0 text-role-rider" />
        <div className="flex flex-col">
          <strong>
            {delivered
              ? t("track.delivered", { time: at("DELIVERED") ?? "" })
              : arrived
                ? t("track.arrived", { society: v.society.name, time: at("AT_SOCIETY") ?? "" })
                : t("track.eta", { society: v.society.name, time: time(v.eta_at) })}
          </strong>
          {!delivered && (
            <span className="text-sm leading-5 text-fg-secondary">
              {arrived
                ? t("track.captain", {
                    captain,
                    time: o.eta_by ? time(o.eta_by) : "",
                  })
                : t("track.rider", { rider: v.rider.name, hub: v.hub.name })}
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
