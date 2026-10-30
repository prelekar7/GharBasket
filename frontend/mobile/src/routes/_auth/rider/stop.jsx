import { Button, EmptyState, Sheet, Skeleton, StatusPill, TextField, toast } from "@gharbasket/ui";
import { NavigationArrowIcon } from "@phosphor-icons/react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { Doors } from "@/features/logistics/Doors.jsx";
import { clock, nextStop, stopDone } from "@/features/logistics/format.js";
import { useMyRoute, useRiderActions } from "@/features/logistics/queries.js";
import { KitchenScan, TiffinList } from "@/features/logistics/Tiffins.jsx";

const SKIP_REASONS = /** @type {const} */ ([
  "captain_absent",
  "no_access",
  "vehicle_issue",
  "other",
]);

/** One society: directions, its crates and bags, and the handover (design RiderStop; M7 §3.3, §3.4). A tiffin route also has kitchen pickups and tiffin drops (§3.7). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/rider/stop")({
  validateSearch: (/** @type {Record<string, unknown>} */ s) => ({
    id: typeof s.id === "string" ? s.id : undefined,
  }),
  component: RiderStop,
});

function RiderStop() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en-IN";
  const navigate = useNavigate();
  const { id } = Route.useSearch();
  const query = useMyRoute(lang);

  if (query.isPending) return <Skeleton lines={8} />;
  if (!query.data) return <ScreenError onRetry={() => query.refetch()} />;
  const route = query.data.route;
  if (!route) return <EmptyState title={t("rider.none.title")} />;
  const stop = route.stops.find((s) => s.id === id) ?? nextStop(route);
  if (!stop) {
    return (
      <EmptyState
        title={t("rider.stop.allDone")}
        body={t("rider.stop.allDoneBody")}
        action={t("rider.stop.toPickups")}
        onAction={() => navigate({ to: "/rider/pickups" })}
      />
    );
  }
  // Keyed by stop, so what was typed never carries over to the next society.
  return <StopDetail key={stop.id} route={route} stop={stop} />;
}

/** @param {{ route: import("@/features/logistics/queries.js").Route, stop: import("@/features/logistics/queries.js").Stop }} props */
function StopDetail({ route, stop }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const actions = useRiderActions();
  const [crates, setCrates] = useState(String(stop.crates));
  const [bags, setBags] = useState(String(stop.bags));
  const [skipping, setSkipping] = useState(false);
  const captain = stop.captain?.display_name ?? null;
  const done = stopDone(stop);
  const started = route.status === "in_progress";
  const kitchen = stop.kind === "kitchen" ? stop.kitchen : null;
  const tiffins = route.kind === "tiffin" && !kitchen; // a tiffin drop: no crates, no counting
  const counted = { crates: Number(crates), bags: Number(bags) };

  const onward = () => {
    const next = route.stops.find((s) => !stopDone(s) && s.id !== stop.id);
    if (next) navigate({ to: "/rider/stop", search: { id: next.id } });
    else navigate({ to: "/rider" });
  };
  const handOver = async () => {
    await actions.handover(route, stop, counted);
    toast(t("rider.stop.handed", { society: stop.society?.name ?? "" }));
  };

  return (
    <div className="flex flex-1 flex-col gap-3.5">
      <FieldHeader
        back={{ label: t("rider.stop.back"), onPress: () => navigate({ to: "/rider" }) }}
        eyebrow={t("rider.stop.of", { n: stop.seq, total: route.stops.length })}
        title={kitchen?.display_name ?? stop.society?.name ?? ""}
        end={
          done && (
            <StatusPill status={stop.status === "skipped" ? "issue" : "delivered"}>
              {stop.status === "skipped" ? t("rider.stop.skipped") : t("rider.card.done")}
            </StatusPill>
          )
        }
      />
      {kitchen && (
        <p className="m-0 text-[15px] leading-[22px] text-fg-secondary">
          {[kitchen.area, t("rider.kitchen.readyBy", { time: clock(kitchen.ready_by) })].join(
            " · ",
          )}
          <br />
          {kitchen.address ?? (started ? null : t("rider.kitchen.addressLater"))}
        </p>
      )}
      {stop.navigate_url && (
        <div className="flex flex-wrap gap-2">
          <Button onPress={() => window.open(stop.navigate_url ?? "", "_blank")}>
            <NavigationArrowIcon aria-hidden size={20} />
            {t("rider.stop.navigate")}
          </Button>
        </div>
      )}
      {(kitchen || tiffins) && (
        <section className="flex flex-col gap-2">
          <h2 className="m-0 text-[19px] leading-[26px] font-bold">
            {t(kitchen ? "rider.kitchen.tiffins" : "rider.tiffin.heading")}
          </h2>
          <TiffinList items={stop.tiffins ?? []} />
          {tiffins && (
            <p className="text-[15px] leading-[22px] text-fg-secondary">
              {captain
                ? t("rider.tiffin.captainCounts", { name: captain, n: stop.bags })
                : t("rider.tiffin.noCaptain")}
            </p>
          )}
        </section>
      )}
      {kitchen && started && (
        <KitchenScan stop={stop} open={!!stop.tiffins?.some((x) => x.status === "to_collect")} />
      )}
      {!kitchen && !tiffins && (
        <section className="flex flex-col gap-2">
          <h2 className="m-0 text-[19px] leading-[26px] font-bold">{t("rider.stop.crates")}</h2>
          <p className="m-0 flex gap-3 text-[15px] leading-[22px]">
            <strong className="rounded-md bg-surface px-3.5 py-2.5 tabular-nums">
              {t("rider.card.crates", { n: stop.crates })}
            </strong>
            <strong className="rounded-md bg-surface px-3.5 py-2.5 tabular-nums">
              {t("rider.card.bags", { n: stop.bags })}
            </strong>
          </p>
          <p className="text-[15px] leading-[22px] text-fg-secondary">
            {captain
              ? t("rider.stop.captainCounts", { name: captain, bags: stop.bags })
              : t("rider.stop.noCaptain")}
          </p>
        </section>
      )}
      {stop.rider_mode && started && (tiffins || stop.status === "handed_over") && (
        <Doors stopId={stop.id} />
      )}
      {!done && started && captain && !kitchen && (
        <section className="flex flex-col gap-1.5 rounded-md bg-surface p-3.5">
          <h2 className="m-0 text-[19px] leading-[26px] font-bold">{t("rider.stop.code")}</h2>
          {stop.handover_code ? (
            <>
              <p className="m-0 font-display text-[40px] leading-[48px] font-bold tracking-[0.3em] tabular-nums">
                {stop.handover_code}
              </p>
              <p className="text-[15px] leading-[22px] text-fg-secondary">
                {t(tiffins ? "rider.tiffin.codeHelp" : "rider.stop.codeHelp", { name: captain })}
              </p>
            </>
          ) : (
            <p className="text-[15px] leading-[22px] text-fg-secondary">
              {t("rider.stop.codeWaiting")}
            </p>
          )}
        </section>
      )}
      {!done && started && !captain && !kitchen && !tiffins && (
        <section className="flex flex-col gap-2.5 rounded-md bg-surface p-3.5">
          <h2 className="m-0 text-[19px] leading-[26px] font-bold">{t("rider.stop.count")}</h2>
          <TextField
            label={t("rider.stop.cratesCounted")}
            inputMode="numeric"
            value={crates}
            onChange={(v) => setCrates(v.replace(/\D/g, "").slice(0, 3))}
          />
          <TextField
            label={t("rider.stop.bagsCounted")}
            inputMode="numeric"
            value={bags}
            onChange={(v) => setBags(v.replace(/\D/g, "").slice(0, 3))}
          />
        </section>
      )}
      <div className="mt-auto flex flex-col gap-2 pt-2">
        {!started && !done && (
          <p className="text-[15px] leading-[22px] text-fg-secondary">
            {t("rider.stop.startFirst")}
          </p>
        )}
        {done ? (
          <Button variant="primary" size="lg" block onPress={onward}>
            {t("rider.stop.back")}
          </Button>
        ) : !started ? (
          <Button variant="primary" size="lg" block onPress={() => actions.start(route)}>
            {t("rider.start")}
          </Button>
        ) : kitchen || (tiffins && !captain) ? null : captain ? (
          stop.status === "pending" ? (
            <Button variant="primary" size="lg" block onPress={() => actions.arrive(route, stop)}>
              {t("rider.stop.arrived")}
            </Button>
          ) : (
            <p role="status" className="text-center font-semibold">
              {t("rider.stop.waiting", { name: captain })}
            </p>
          )
        ) : (
          <Button
            variant="primary"
            size="lg"
            block
            isDisabled={!crates || !bags}
            onPress={handOver}
          >
            {t("rider.stop.done")}
          </Button>
        )}
        {started && !done && (
          <Button variant="quiet" block onPress={() => setSkipping(true)}>
            {t("rider.stop.skip")}
          </Button>
        )}
      </div>
      <Sheet open={skipping} onOpenChange={setSkipping} title={t("rider.stop.skipTitle")}>
        <div className="flex flex-col gap-2">
          {SKIP_REASONS.map((reason) => (
            <Button
              key={reason}
              block
              onPress={async () => {
                setSkipping(false);
                await actions.skip(route, stop, reason);
                onward();
              }}
            >
              {t(`rider.stop.skipReasons.${reason}`)}
            </Button>
          ))}
        </div>
      </Sheet>
    </div>
  );
}
