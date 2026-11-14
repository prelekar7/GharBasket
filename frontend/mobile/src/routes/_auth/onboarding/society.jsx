import { Button, Chip, IconButton, RadioCards, Skeleton, TextField } from "@gharbasket/ui";
import { MagnifyingGlassIcon } from "@phosphor-icons/react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import {
  useInvitedSociety,
  useSocieties,
  useSocietyUnlocks,
} from "@/features/community/queries.js";
import { nextStep, useOnboardingDraft } from "@/features/onboarding/draft.js";

/** Which society do you live in? (design Hf04) */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/society")({ component: SocietyPicker });

const km = (/** @type {number} */ m) =>
  (m / 1000).toLocaleString("en-IN", { maximumFractionDigits: 1 });

function SocietyPicker() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { societyId, inviteSlug, update } = useOnboardingDraft();
  // A neighbour's invite (/join?s=) puts that society first and chooses it, unless one is already chosen.
  const invite = societyId ? null : inviteSlug;
  const [typed, setTyped] = useState("");
  const [q, setQ] = useState("");
  const [near, setNear] = useState(/** @type {{ lat: number, lng: number } | null} */ (null));
  const { data, isPending, isError, refetch } = useSocieties({ q, near });

  const invitation = useInvitedSociety(invite);
  const invited = invitation.data ?? null;
  const found = data?.items ?? [];
  // The invited society leads the list, whatever the search: it may be a prospect society far away.
  const items = invited ? [invited, ...found.filter((s) => s.id !== invited.id)] : found;
  // The list has no unlock counts (and never a captain), so ask for each society's own.
  const unlocks = useSocietyUnlocks(items.map((s) => s.id));
  const selected =
    societyId && items.some((s) => s.id === societyId)
      ? societyId
      : (invited?.id ?? items[0]?.id ?? "");
  // Another society's towers and flats are different: a new choice clears the old tower and flat.
  const choose = (/** @type {string} */ id) =>
    update(id === societyId ? { societyId: id } : { societyId: id, towerId: null, flat: "" });

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
              if (inviteSlug) update({ inviteSlug: null });
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
      {invited && (
        <p role="note" className="rounded-md border border-accent bg-accent-soft px-3 py-2 text-sm">
          {t("onboarding.society.invited", { name: invited.name })}
        </p>
      )}
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
      {isPending || (!!invite && invitation.isPending) ? (
        <Skeleton lines={3} />
      ) : isError ? (
        <ScreenError onRetry={() => refetch()} />
      ) : (
        <RadioCards
          label={t("onboarding.society.near")}
          value={selected}
          onChange={choose}
          options={items.map((s) => {
            const unlock = unlocks.get(s.id);
            return {
              value: s.id,
              title: s.name,
              description: [
                s.area,
                s.distance_m === null ? null : t("onboarding.society.km", { km: km(s.distance_m) }),
                unlock && s.status !== "live" && unlock.joined > 0
                  ? t("onboarding.society.joined", {
                      joined: unlock.joined,
                      target: unlock.threshold,
                    })
                  : null,
              ]
                .filter(Boolean)
                .join(" · "),
              badge:
                s.status === "live"
                  ? t("onboarding.society.delivering")
                  : s.status === "unlocking"
                    ? t("onboarding.society.unlocking")
                    : t("onboarding.society.soon"),
            };
          })}
        />
      )}
    </OnboardingFrame>
  );
}
