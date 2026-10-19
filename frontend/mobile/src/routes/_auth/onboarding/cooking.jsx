import { Button, Chip, SegmentedControl } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { useOnboardingDraft } from "@/features/onboarding/draft.js";
import { useStepGo } from "@/features/onboarding/useStepGo.js";

const MINUTES = ["15", "30", "45", "60"];
const DAYS = [0, 1, 2, 3, 4, 5, 6]; // Monday … Sunday

/** How much time do you have to cook? Busy days get quicker recipes or a kit (design Hf10). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/cooking")({ component: Cooking });

function Cooking() {
  const { t } = useTranslation();
  const _navigate = useNavigate();
  const { household: h, updateHousehold } = useOnboardingDraft();
  const step = useStepGo("/onboarding/cooking");
  const times = MINUTES.map((value) => ({ value, label: t(`onboarding.cooking.times.m${value}`) }));
  const label = "text-sm leading-5 font-semibold";

  return (
    <OnboardingFrame
      step={4}
      footer={
        <Button variant="primary" size="lg" block isPending={step.busy} onPress={step.go}>
          {step.label}
        </Button>
      }
    >
      <div className="flex flex-col gap-2">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {t("onboarding.cooking.title")}
        </h1>
        <p className="text-fg-secondary">{t("onboarding.cooking.sub")}</p>
      </div>
      <div className="flex flex-col gap-2">
        <span className={label}>{t("onboarding.cooking.weekdays")}</span>
        <SegmentedControl
          label={t("onboarding.cooking.weekdays")}
          value={String(h.weekdayCookMinutes)}
          onChange={(v) => updateHousehold({ weekdayCookMinutes: Number(v) })}
          options={times}
        />
      </div>
      <div className="flex flex-col gap-2">
        <span className={label}>{t("onboarding.cooking.weekends")}</span>
        <SegmentedControl
          label={t("onboarding.cooking.weekends")}
          value={String(h.weekendCookMinutes)}
          onChange={(v) => updateHousehold({ weekendCookMinutes: Number(v) })}
          options={times}
        />
      </div>
      <div className="flex flex-col gap-2">
        <span className={label}>{t("onboarding.cooking.noCook")}</span>
        <p className="-mt-1 text-sm leading-[22px] text-fg-secondary">
          {t("onboarding.cooking.noCookNote")}
        </p>
        <div className="flex flex-wrap gap-2">
          {DAYS.map((d) => (
            <Chip
              key={d}
              isSelected={h.noCookDays.includes(d)}
              onChange={(on) =>
                updateHousehold({
                  noCookDays: on
                    ? [...h.noCookDays, d].sort()
                    : h.noCookDays.filter((x) => x !== d),
                })
              }
            >
              {t(`onboarding.cooking.days.d${d}`)}
            </Chip>
          ))}
        </div>
      </div>
    </OnboardingFrame>
  );
}
