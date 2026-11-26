import { Button, Select, Skeleton, SocietyUnlockBar, TextField } from "@gharbasket/ui";
import { MedalIcon } from "@phosphor-icons/react";
import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { useSociety, useSocietyUnlock } from "@/features/community/queries.js";
import { isValidName, NAME_MAX, NAME_MIN } from "@/features/identity/name.js";
import { nextStep, useOnboardingDraft } from "@/features/onboarding/draft.js";
import { useSession } from "@/lib/session.js";

/**
 * Tower and flat in the chosen society, and who its captain is (design Hf06). While the profile has
 * no name, it also asks for the owner's own name (#492): the API saves `owner_name` only into an
 * empty profile name, so asking someone who already has one would quietly drop the answer.
 */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/flat")({
  beforeLoad: () => {
    if (!useOnboardingDraft.getState().societyId) throw redirect({ to: "/onboarding/society" });
  },
  component: FlatStep,
});

function FlatStep() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { societyId, towerId, flat, ownerName, update } = useOnboardingDraft();
  const askName = !useSession((s) => s.user?.name);
  const { data: society, isPending, isError, refetch } = useSociety(societyId ?? undefined);
  const captain = society?.captain;
  const { data: unlock } = useSocietyUnlock(society && !captain ? society.id : undefined);
  const nameOk = !askName || isValidName(ownerName);
  const valid = !!towerId && /^[A-Za-z0-9-]{1,8}$/.test(flat.trim()) && nameOk;

  return (
    <OnboardingFrame
      footer={
        <Button
          variant="primary"
          size="lg"
          block
          isDisabled={!valid}
          onPress={() => navigate({ to: nextStep("/onboarding/flat") })}
        >
          {t("onboarding.continue")}
        </Button>
      }
    >
      {isPending ? (
        <Skeleton lines={4} />
      ) : isError || !society ? (
        <ScreenError onRetry={() => refetch()} />
      ) : (
        <>
          <div className="flex flex-col gap-0.5">
            <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
              {t("onboarding.flat.eyebrow")}
            </span>
            <h1 className="font-display text-[28px] leading-[34px] font-semibold">
              {society.name}
            </h1>
            <p className="text-sm text-fg-secondary">{society.area}</p>
          </div>
          <Select
            label={t("onboarding.flat.tower")}
            placeholder={t("onboarding.flat.towerPh")}
            value={towerId ?? ""}
            onChange={(id) => update({ towerId: id })}
            options={society.towers.map((tw) => ({ value: tw.id, label: tw.label }))}
          />
          <TextField
            label={t("onboarding.flat.flat")}
            inputMode="text"
            autoComplete="off"
            value={flat}
            onChange={(v) => update({ flat: v.toUpperCase() })}
          />
          {askName && (
            <TextField
              label={t("onboarding.flat.name")}
              description={t("onboarding.flat.nameHint")}
              autoComplete="name"
              value={ownerName}
              onChange={(v) => update({ ownerName: v })}
              isInvalid={ownerName.trim() !== "" && !nameOk}
              errorMessage={t("onboarding.flat.nameError", { min: NAME_MIN, max: NAME_MAX })}
            />
          )}
          {captain ? (
            <section className="flex items-start gap-3.5 rounded-md bg-surface p-4">
              <div className="grid size-13 shrink-0 place-items-center rounded-full bg-accent-soft text-accent-fg">
                <MedalIcon aria-hidden size={28} />
              </div>
              <div className="flex flex-col gap-0.5">
                <strong>{t("onboarding.flat.capTitle", { name: captain.display_name })}</strong>
                <p className="text-sm leading-5 text-fg-secondary">
                  {t("onboarding.flat.capBody")}
                </p>
              </div>
            </section>
          ) : (
            unlock && (
              <SocietyUnlockBar
                society={society.name}
                joined={unlock.joined}
                target={unlock.threshold}
                labels={{
                  count: t("onboarding.unlock.count", {
                    joined: unlock.joined,
                    target: unlock.threshold,
                  }),
                  remaining: t("onboarding.unlock.remaining", {
                    n: Math.max(unlock.threshold - unlock.joined, 0),
                  }),
                  invite: t("onboarding.unlock.invite"),
                  progress: t("onboarding.unlock.progress"),
                }}
              />
            )
          )}
        </>
      )}
    </OnboardingFrame>
  );
}
