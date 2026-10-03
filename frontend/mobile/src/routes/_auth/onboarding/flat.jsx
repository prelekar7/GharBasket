import { Button, ErrorState, Select, Skeleton, SocietyUnlockBar, TextField } from "@gharbasket/ui";
import { MedalIcon } from "@phosphor-icons/react";
import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { useSociety } from "@/features/community/queries.js";
import { nextStep, useOnboardingDraft } from "@/features/onboarding/draft.js";

/** Tower and flat in the chosen society, and who its captain is (design Hf06). */
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
  const { societyId, towerId, flat, update } = useOnboardingDraft();
  const { data: society, isPending, isError, refetch } = useSociety(societyId ?? undefined);
  const valid = !!towerId && /^[A-Za-z0-9-]{1,8}$/.test(flat.trim());

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
        <ErrorState
          title={t("onboarding.error")}
          retryLabel={t("onboarding.retry")}
          onRetry={() => refetch()}
        />
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
            inputMode="numeric"
            autoComplete="off"
            value={flat}
            onChange={(v) => update({ flat: v })}
          />
          {society.captain ? (
            <section className="flex items-start gap-3.5 rounded-md bg-surface p-4">
              <div className="grid size-13 shrink-0 place-items-center rounded-full bg-accent-soft text-accent-fg">
                <MedalIcon aria-hidden size={28} />
              </div>
              <div className="flex flex-col gap-0.5">
                <strong>{t("onboarding.flat.capTitle", { name: society.captain.name })}</strong>
                <p className="text-sm leading-5 text-fg-secondary">
                  {t("onboarding.flat.capBody", { flat: society.captain.flat })}
                </p>
              </div>
            </section>
          ) : (
            <SocietyUnlockBar
              society={society.name}
              joined={society.unlock.joined}
              target={society.unlock.threshold}
              labels={{
                count: t("onboarding.unlock.count", {
                  joined: society.unlock.joined,
                  target: society.unlock.threshold,
                }),
                remaining: t("onboarding.unlock.remaining", {
                  n: society.unlock.threshold - society.unlock.joined,
                }),
                invite: t("onboarding.unlock.invite"),
                progress: t("onboarding.unlock.progress"),
              }}
            />
          )}
        </>
      )}
    </OnboardingFrame>
  );
}
