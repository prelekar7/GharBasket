import { Button, TextField, toast } from "@gharbasket/ui";
import { useMutation } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { lazy, Suspense, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { ApiError, request } from "@/lib/api.js";

// MapLibre is large, so it loads only on this screen.
const PinMap = lazy(() => import("@/components/PinMap.jsx"));
const KHARGHAR = { lat: 19.033, lng: 73.071 };
const MIN_NAME = 2; // the API takes 2 to 120 characters

/** The design's map illustration: shown while the map loads, and on phones without WebGL2. */
function MapSketch() {
  return (
    <svg
      viewBox="0 0 350 210"
      aria-hidden
      className="block h-[210px] w-full rounded-md border border-line bg-surface"
    >
      <path
        d="M-10 150C80 130 160 140 360 96M120 -10C130 70 140 140 118 220"
        className="fill-none stroke-line-strong opacity-45"
        strokeWidth="10"
        strokeLinecap="round"
      />
      <path
        d="M-10 60h380M240 -10v230M30 -10l40 230"
        className="fill-none stroke-line"
        strokeWidth="4"
        strokeLinecap="round"
      />
      {[
        [150, 30, 70, 52],
        [256, 20, 80, 58],
        [150, 160, 64, 40],
        [260, 120, 70, 70],
        [44, 76, 56, 48],
      ].map(([x, y, w, h]) => (
        <rect key={`${x}-${y}`} x={x} y={y} width={w} height={h} rx="6" className="fill-line" />
      ))}
      <path
        d="M188 58c-12 0-21 9-21 20.5 0 15 21 34.5 21 34.5s21-19.5 21-34.5C209 67 200 58 188 58z"
        className="fill-primary"
      />
      <circle cx="188" cy="78.5" r="7" className="fill-on-primary" />
    </svg>
  );
}

/** Request a society we don't serve yet, with a pin on the building (design Hf05, M9). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/request")({ component: RequestSociety });

function RequestSociety() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [area, setArea] = useState("");
  // The map opens on Kharghar, but that is only where the map starts: no pin moved, no location.
  const [point, setPoint] = useState(/** @type {{ lat: number, lng: number } | null} */ (null));
  // One key per request: a retry of the same request reuses it, an edited one gets its own.
  const attempt = useRef({ key: crypto.randomUUID(), body: "" });
  const send = useMutation({
    mutationFn: () => {
      const body = {
        name: name.trim(),
        area: area.trim() || null,
        location: point && { lat: point.lat, lng: point.lng },
      };
      const text = JSON.stringify(body);
      if (attempt.current.body !== text) attempt.current = { key: crypto.randomUUID(), body: text };
      return request("/v1/society-requests", {
        method: "POST",
        headers: { "idempotency-key": attempt.current.key },
        body,
      });
    },
    onSuccess: () => {
      toast(t("onboarding.request.sent"));
      navigate({ to: "/onboarding/society" });
    },
  });

  return (
    <OnboardingFrame
      footer={
        <>
          <p className="text-sm leading-5 text-fg-secondary">{t("onboarding.request.note")}</p>
          {send.isError && (
            <p role="alert" className="text-sm font-semibold text-danger">
              {send.error instanceof ApiError &&
              send.error.code === "community.request_rate_limited"
                ? t("onboarding.request.limited")
                : t("onboarding.error")}
            </p>
          )}
          <Button
            variant="primary"
            size="lg"
            block
            isDisabled={name.trim().length < MIN_NAME}
            isPending={send.isPending}
            onPress={() => send.mutate()}
          >
            {t("onboarding.request.cta")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {t("onboarding.request.title")}
        </h1>
        <p className="text-fg-secondary">{t("onboarding.request.sub")}</p>
      </div>
      <figure className="flex flex-col gap-1.5">
        <Suspense fallback={<MapSketch />}>
          <PinMap
            center={KHARGHAR}
            onChange={setPoint}
            label={t("onboarding.request.caption")}
            fallback={<MapSketch />}
          />
        </Suspense>
        <figcaption className="text-sm leading-5 text-fg-secondary">
          {t("onboarding.request.caption")}
        </figcaption>
      </figure>
      <TextField
        label={t("onboarding.request.name")}
        placeholder={t("onboarding.request.namePh")}
        value={name}
        onChange={setName}
        maxLength={120}
      />
      <TextField
        label={t("onboarding.request.area")}
        placeholder={t("onboarding.request.areaPh")}
        value={area}
        onChange={setArea}
        maxLength={120}
      />
    </OnboardingFrame>
  );
}
