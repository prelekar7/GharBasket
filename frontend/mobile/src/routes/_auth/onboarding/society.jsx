import {
  Button,
  Chip,
  ErrorState,
  IconButton,
  RadioCards,
  Skeleton,
  TextField,
} from "@gharbasket/ui";
import { MagnifyingGlassIcon } from "@phosphor-icons/react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { useSocieties } from "@/features/community/queries.js";
import { nextStep, useOnboardingDraft } from "@/features/onboarding/draft.js";

/** Which society do you live in? (design Hf04) */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/society")({ component: SocietyPicker });

const km = (/** @type {number} */ m) =>
  (m / 1000).toLocaleString("en-IN", { maximumFractionDigits: 1 });

function SocietyPicker() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { societyId, update } = useOnboardingDraft();
  const [typed, setTyped] = useState("");
  const [q, setQ] = useState("");
  const [near, setNear] = useState(/** @type {{ lat: number, lng: number } | null} */ (null));
  const { data, isPending, isError, refetch } = useSocieties({ q, near });

  const items = data?.items ?? [];
  const selected =
    societyId && items.some((s) => s.id === societyId) ? societyId : (items[0]?.id ?? "");
  /** A different society has different towers, so its tower and flat start empty. @param {string} id */
  const choose = (id) => id !== societyId && update({ societyId: id, towerId: null, flat: "" });
  /** @type {Record<import("@/features/community/queries.js").SocietyStatus, string>} */
  const BADGE = {
    live: "onboarding.society.delivering",
    unlocking: "onboarding.society.unlocking",
    prospect: "onboarding.society.soon",
    paused: "onboarding.society.soon",
  };

  const locate = () =>
    navigator.geolocation?.getCurrentPosition(
      (p) => setNear({ lat: p.coords.latitude, lng: p.coords.longitude }),
      () => setNear(null),
      { timeout: 10_000 },
    );

  return (
    <OnboardingFrame
      footer={
        <>
          <Button variant="quiet" block onPress={() => navigate({ to: "/onboarding/request" })}>
            {t("onboarding.society.missing")}
          </Button>
          <Button
            variant="primary"
            size="lg"
            block
            isDisabled={!selected}
            onPress={() => {
              choose(selected);
              navigate({ to: nextStep("/onboarding/society") });
            }}
          >
            {t("onboarding.continue")}
          </Button>
        </>
      }
    >
      <h1 className="font-display text-[28px] leading-[34px] font-semibold text-balance">
        {t("onboarding.society.title")}
      </h1>
      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setQ(typed.trim());
        }}
      >
        <TextField
          label={t("onboarding.society.search")}
          placeholder={t("onboarding.society.placeholder")}
          value={typed}
          onChange={setTyped}
          className="flex-1"
        />
        <IconButton label={t("onboarding.society.search")} type="submit">
          <MagnifyingGlassIcon size={24} />
        </IconButton>
      </form>
      <div>
        <Chip isSelected={!!near} onChange={(on) => (on ? locate() : setNear(null))}>
          {t("onboarding.society.locate")}
        </Chip>
      </div>
      <span className="text-sm leading-5 font-semibold">{t("onboarding.society.near")}</span>
      {isPending ? (
        <Skeleton lines={3} />
      ) : isError ? (
        <ErrorState
          title={t("onboarding.error")}
          retryLabel={t("onboarding.retry")}
          onRetry={() => refetch()}
        />
      ) : (
        <RadioCards
          label={t("onboarding.society.near")}
          value={selected}
          onChange={choose}
          options={items.map((s) => ({
            value: s.id,
            title: s.name,
            description: [
              s.area,
              s.distance_m === null ? null : t("onboarding.society.km", { km: km(s.distance_m) }),
            ]
              .filter(Boolean)
              .join(" · "),
            badge: t(BADGE[s.status]),
          }))}
        />
      )}
    </OnboardingFrame>
  );
}
