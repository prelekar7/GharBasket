import {
  Button,
  EmptyBasket,
  EmptyState,
  ErrorState,
  OfflineBanner,
  RouteStopCard,
  Skeleton,
} from "@gharbasket/ui";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { lazy, Suspense } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { clock, dayLabel, mapsLink, nextStop } from "@/features/logistics/format.js";
import { useCachedHubManager, useMyRoute, useRiderActions } from "@/features/logistics/queries.js";
import { clockTime, istClock, timeRange, todayIst } from "@/features/subscriptions/format.js";
import { useWaitingCount } from "@/lib/outbox.js";

const RouteMap = lazy(() => import("@/features/logistics/RouteMap.jsx"));

/** Today's route: map, stops in order, the way back, start and finish (designs RiderRoute, RiderNoRoute, RiderError; M7 US-2). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/rider/")({ component: RiderRoute });

function RiderRoute() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const navigate = useNavigate();
  const query = useMyRoute(lang);
  const actions = useRiderActions();
  const manager = useCachedHubManager(lang);
  const waiting = useWaitingCount();
  const today = todayIst();

  if (query.isPending) return <Skeleton lines={8} />;
  if (!query.data) {
    return (
      <>
        {!navigator.onLine && waiting === 0 && <OfflineBanner message={t("rider.offlineNow")} />}
        <FieldHeader
          eyebrow={`${dayLabel(today, lang)} · ${clockTime(istClock(new Date().toISOString()), lang)}`}
          title={t("rider.title")}
        />
        <div className="flex flex-col gap-4 pt-4">
          <ErrorState
            title={t("rider.error.title")}
            body={t("rider.error.body")}
            retryLabel={t("rider.error.retry")}
            onRetry={() => query.refetch()}
            support={
              manager ? (
                <a href={`tel:${manager.phone}`} className="font-semibold text-link underline">
                  {t("rider.error.supportCall", { name: manager.name })}
                </a>
              ) : (
                <Link to="/help" className="font-semibold text-link underline">
                  {t("rider.error.support")}
                </Link>
              )
            }
          />
          <p className="text-[15px] leading-[22px] text-fg-secondary">{t("rider.error.tip")}</p>
        </div>
      </>
    );
  }

  const route = query.data.items[0];
  if (!route) {
    const next = query.data.next;
    return (
      <>
        <FieldHeader eyebrow={dayLabel(today, lang)} title={t("rider.title")} />
        <div className="pt-4">
          <EmptyState
            illustration={<EmptyBasket />}
            title={t("rider.none.title")}
            body={
              next
                ? t("rider.none.body", {
                    day: dayLabel(next.date, lang),
                    slot: timeRange(next, lang),
                  })
                : t("rider.none.bodyLater")
            }
            action={t("rider.none.earnings")}
            onAction={() => navigate({ to: "/rider/earnings" })}
          />
        </div>
      </>
    );
  }

  const current = route.status === "started" ? nextStop(route) : null;
  const donations = route.pickups.filter((p) => p.kind === "donation").length;
  const toStop = (/** @type {string} */ id) => navigate({ to: "/rider/stop", search: { id } });
  return (
    <div className="flex flex-1 flex-col gap-2.5">
      <FieldHeader
        eyebrow={t("rider.when", {
          day: dayLabel(route.date, lang),
          slot: timeRange(route.slot, lang),
          plate: route.vehicle.plate,
        })}
        title={t("rider.title")}
      />
      <Suspense fallback={<Skeleton className="h-[180px] w-full" />}>
        <RouteMap
          route={route}
          label={t("rider.map.label", { count: route.stops.length })}
          fallback={
            <figure className="m-0 grid h-[180px] place-items-center rounded-md border border-dashed border-line-strong bg-surface p-4 text-center">
              <figcaption className="text-[15px] leading-[22px] text-fg-secondary">
                {t("rider.map.fallback")}
              </figcaption>
            </figure>
          }
        />
      </Suspense>
      <ol className="m-0 flex list-none flex-col gap-2.5 p-0">
        {route.stops.map((s) => {
          const status = s.status === "done" ? "done" : s.id === current?.id ? "current" : "todo";
          const card = (
            <RouteStopCard
              n={s.seq}
              society={s.society.name}
              where={s.society.where}
              captain={s.captain?.name}
              window={clock(s.eta)}
              crates={s.crates.length}
              bags={s.bags_count}
              status={status}
              onNavigate={() => window.open(mapsLink(s.society), "_blank")}
              onCall={() => s.captain && window.open(`tel:${s.captain.phone}`)}
              labels={{
                stop: t("rider.card.stop", { n: s.seq }),
                crate: t("rider.card.crates", { n: s.crates.length }),
                crates: t("rider.card.crates", { n: s.crates.length }),
                bag: t("rider.card.bags", { n: s.bags_count }),
                bags: t("rider.card.bags", { n: s.bags_count }),
                captain: t("rider.card.captain", { name: s.captain?.name ?? "" }),
                navigate: t("rider.stop.navigate"),
                call: t("rider.stop.call", { name: s.captain?.name ?? "" }),
                todo: t("rider.card.todo"),
                current: t("rider.card.current"),
                done: t("rider.card.done"),
                issue: t("rider.card.issue"),
              }}
            />
          );
          return (
            <li key={s.id}>
              {status === "current" ? (
                card
              ) : (
                <Link
                  to="/rider/stop"
                  search={{ id: s.id }}
                  className="block rounded-md text-inherit no-underline outline-none focus-visible:shadow-focus"
                >
                  {card}
                </Link>
              )}
            </li>
          );
        })}
      </ol>
      <p className="text-[15px] leading-[22px] text-fg-secondary">
        <strong className="text-fg">
          {t("rider.back", { time: clock(route.back.eta), hub: route.hub.name })}
        </strong>{" "}
        · {t("rider.backDetail", { count: donations })}
      </p>
      {route.status === "published" && (
        <p className="text-[15px] leading-[22px] text-fg-secondary">{t("rider.offline")}</p>
      )}
      <div className="mt-auto pt-2">
        {route.status === "published" && (
          <Button variant="primary" size="lg" block onPress={() => actions.start(route)}>
            {t("rider.start")}
          </Button>
        )}
        {route.status === "started" && current && (
          <Button variant="primary" size="lg" block onPress={() => toStop(current.id)}>
            {t("rider.goTo", { n: current.seq })}
          </Button>
        )}
        {route.status === "started" && !current && (
          <Button variant="primary" size="lg" block onPress={() => actions.complete(route)}>
            {t("rider.finish")}
          </Button>
        )}
        {route.status === "completed" && (
          <p role="status" className="text-center font-semibold">
            {t("rider.finished")}
          </p>
        )}
      </div>
    </div>
  );
}
