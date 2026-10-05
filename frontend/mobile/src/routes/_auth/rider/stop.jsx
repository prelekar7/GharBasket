import {
  Button,
  CrateCounter,
  EmptyState,
  Skeleton,
  StatusPill,
  TextField,
  toast,
} from "@gharbasket/ui";
import { CameraIcon, NavigationArrowIcon, PhoneIcon } from "@phosphor-icons/react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { FieldHeader } from "@/components/FieldFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { mapsLink, nextStop } from "@/features/logistics/format.js";
import { smallJpeg } from "@/features/logistics/photo.js";
import { useMyRoute, useRiderActions } from "@/features/logistics/queries.js";

/** One society: directions, its crates and the handover proof, a code or a photo (design RiderStop; M7 US-3). */
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
  const route = query.data.items[0];
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
  // Keyed by stop, so the code and photo never carry over to the next society.
  return <StopDetail key={stop.id} route={route} stop={stop} />;
}

/** @param {{ route: import("@/features/logistics/queries.js").Route, stop: import("@/features/logistics/queries.js").RouteStop }} props */
function StopDetail({ route, stop }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const actions = useRiderActions();
  const camera = useRef(/** @type {HTMLInputElement | null} */ (null));
  const [otp, setOtp] = useState("");
  const [photo, setPhoto] = useState(/** @type {string | null} */ (null));
  const [photoFailed, setPhotoFailed] = useState(false);
  const captain = stop.captain?.name ?? null;
  const done = stop.status === "done";
  const started = route.status === "started";
  const hasProof = /^\d{4}$/.test(otp) || !!photo;

  const handOver = async () => {
    await actions.handover(route, stop, photo && !otp ? { photo_jpeg: photo } : { otp });
    toast(t("rider.stop.handed", { society: stop.society.name }));
    const next = route.stops.find((s) => s.status !== "done" && s.id !== stop.id);
    if (next) navigate({ to: "/rider/stop", search: { id: next.id } });
    else navigate({ to: "/rider" });
  };

  return (
    <div className="flex flex-1 flex-col gap-3.5">
      <FieldHeader
        back={{ label: t("rider.stop.back"), onPress: () => navigate({ to: "/rider" }) }}
        eyebrow={[t("rider.stop.of", { n: stop.seq, total: route.stops.length }), stop.society.gate]
          .filter(Boolean)
          .join(" · ")}
        title={stop.society.name}
        end={done && <StatusPill status="delivered">{t("rider.card.done")}</StatusPill>}
      />
      <div className="flex flex-wrap gap-2">
        <Button onPress={() => window.open(mapsLink(stop.society), "_blank")}>
          <NavigationArrowIcon aria-hidden size={20} />
          {t("rider.stop.navigate")}
        </Button>
        {stop.captain && (
          <Button variant="quiet" onPress={() => window.open(`tel:${stop.captain?.phone}`)}>
            <PhoneIcon aria-hidden size={20} />
            {t("rider.stop.call", { name: captain })}
          </Button>
        )}
      </div>
      <section className="flex flex-col gap-2">
        <h2 className="m-0 text-[19px] leading-[26px] font-bold">{t("rider.stop.crates")}</h2>
        <CrateCounter
          crates={stop.crates}
          labels={{
            label: t("rider.stop.crateLabels.label"),
            scanned: t("rider.stop.crateLabels.scanned"),
            waiting: t("rider.stop.crateLabels.waiting"),
            missing: t("rider.stop.crateLabels.missing"),
            bag: t("rider.stop.crateLabels.bag"),
            bags: t("rider.stop.crateLabels.bags"),
            summary: t("rider.stop.crateLabels.summary"),
          }}
        />
        <p className="text-[15px] leading-[22px] text-fg-secondary">
          {captain
            ? t("rider.stop.captainScans", { name: captain, bags: stop.bags_count })
            : t("rider.stop.noCaptain")}
        </p>
      </section>
      {!done && (
        <section className="flex flex-col gap-2.5 rounded-md bg-surface p-3.5">
          <h2 className="m-0 text-[19px] leading-[26px] font-bold">{t("rider.stop.proof")}</h2>
          {captain && (
            <TextField
              label={t("rider.stop.otp", { name: captain })}
              description={t("rider.stop.otpHelp")}
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={4}
              placeholder="• • • •"
              value={otp}
              onChange={(v) => setOtp(v.replace(/\D/g, "").slice(0, 4))}
            />
          )}
          <input
            ref={camera}
            type="file"
            accept="image/*"
            capture="environment"
            hidden
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              const small = await smallJpeg(file).catch(() => null);
              setPhotoFailed(!small);
              setPhoto(small);
            }}
          />
          {photo && (
            <div className="flex items-center gap-3">
              <img
                src={photo}
                alt={t("rider.stop.photoAlt")}
                className="size-16 rounded-sm object-cover"
              />
              <span className="font-semibold">{t("rider.stop.photoAdded")}</span>
            </div>
          )}
          {photoFailed && (
            <p role="alert" className="text-sm font-semibold text-danger">
              {t("rider.stop.photoFailed")}
            </p>
          )}
          <Button block onPress={() => camera.current?.click()}>
            <CameraIcon aria-hidden size={20} />
            {photo ? t("rider.stop.photoAgain") : t("rider.stop.photo")}
          </Button>
        </section>
      )}
      <div className="mt-auto flex flex-col gap-2 pt-2">
        {!started && !done && (
          <p className="text-[15px] leading-[22px] text-fg-secondary">
            {t("rider.stop.startFirst")}
          </p>
        )}
        {done ? (
          <Button variant="primary" size="lg" block onPress={() => navigate({ to: "/rider" })}>
            {t("rider.stop.back")}
          </Button>
        ) : !started ? (
          <Button variant="primary" size="lg" block onPress={() => actions.start(route)}>
            {t("rider.start")}
          </Button>
        ) : (
          <Button variant="primary" size="lg" block isDisabled={!hasProof} onPress={handOver}>
            {t("rider.stop.done")}
          </Button>
        )}
      </div>
    </div>
  );
}
