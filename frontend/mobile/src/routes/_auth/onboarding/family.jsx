import { Button, QtyStepper } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { nextStep, useOnboardingDraft } from "@/features/onboarding/draft.js";

/** @type {("adults" | "seniors" | "children" | "little")[]} */
const GROUPS = ["adults", "seniors", "children", "little"];

/** Who eats at home? Portions are planned per person (design Hf07). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/family")({ component: Family });

function Family() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { household, updateHousehold } = useOnboardingDraft();
  const { members } = household;
  const total = GROUPS.reduce((n, g) => n + members[g], 0);

  return (
    <OnboardingFrame
      step={1}
      footer={
        <Button
          variant="primary"
          size="lg"
          block
          isDisabled={total === 0}
          onPress={() => navigate({ to: nextStep("/onboarding/family") })}
        >
          {t("onboarding.next")}
        </Button>
      }
    >
      <div className="flex flex-col gap-2">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {t("onboarding.family.title")}
        </h1>
        <p className="text-fg-secondary">{t("onboarding.family.sub")}</p>
      </div>
      <div className="flex flex-col">
        {GROUPS.map((g) => (
          <div
            key={g}
            className="flex min-h-[72px] items-center justify-between gap-3 border-b border-line"
          >
            <div className="flex flex-col">
              <strong>{t(`onboarding.family.${g}`)}</strong>
              <span className="text-sm text-fg-secondary">{t(`onboarding.family.${g}Ages`)}</span>
            </div>
            <QtyStepper
              label={t(`onboarding.family.${g}`)}
              value={members[g]}
              min={0}
              max={10}
              onChange={(n) => updateHousehold({ members: { ...members, [g]: n } })}
              labels={{ decrease: t("onboarding.decrease"), increase: t("onboarding.increase") }}
            />
          </div>
        ))}
      </div>
      <p className="text-sm leading-5 text-fg-secondary">
        {t("onboarding.family.total", { count: total })}
      </p>
    </OnboardingFrame>
  );
}
