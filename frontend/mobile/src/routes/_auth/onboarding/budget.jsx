import { Button, Chip, formatPaise, Switch, TextField } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { useOnboardingDraft } from "@/features/onboarding/draft.js";
import { useStepGo } from "@/features/onboarding/useStepGo.js";

const QUICK_RUPEES = [2500, 3000, 3500, 4000];
const DEFAULT_PAISE = 300000;
const rupees = new Intl.NumberFormat("en-IN");

/** Budget Lock: an optional monthly cap on the vegetable bill (design Hf11, M3 budget_locks). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/budget")({ component: Budget });

function Budget() {
  const { t } = useTranslation();
  const _navigate = useNavigate();
  const { household: h, updateHousehold } = useOnboardingDraft();
  const step = useStepGo("/onboarding/budget");
  const on = h.budgetLockPaise != null;
  const paise = h.budgetLockPaise ?? DEFAULT_PAISE;
  const go = step.go;

  return (
    <OnboardingFrame
      step={5}
      footer={
        <>
          <Button
            variant="primary"
            size="lg"
            block
            isPending={step.busy}
            isDisabled={on && paise < 50000}
            onPress={go}
          >
            {step.label}
          </Button>
          {!step.editing && (
            <Button
              variant="quiet"
              block
              onPress={() => {
                updateHousehold({ budgetLockPaise: null });
                go();
              }}
            >
              {t("onboarding.budget.later")}
            </Button>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
          {t("onboarding.budget.optional")}
        </span>
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          {t("onboarding.budget.title")}
        </h1>
        <p className="text-fg-secondary">{t("onboarding.budget.sub")}</p>
      </div>
      <Switch
        label={t("onboarding.budget.toggle")}
        isSelected={on}
        onChange={(v) => updateHousehold({ budgetLockPaise: v ? paise : null })}
      />
      {on && (
        <>
          <TextField
            label={t("onboarding.budget.amount")}
            prefix="₹"
            inputMode="numeric"
            value={rupees.format(paise / 100)}
            onChange={(v) =>
              updateHousehold({
                budgetLockPaise: Number(v.replace(/\D/g, "").slice(0, 6) || 0) * 100,
              })
            }
            isInvalid={paise < 50000}
            errorMessage={t("onboarding.budget.min", { amount: formatPaise(50000) })}
          />
          <div className="flex flex-wrap gap-2">
            {QUICK_RUPEES.map((r) => (
              <Chip
                key={r}
                isSelected={paise === r * 100}
                onChange={() => updateHousehold({ budgetLockPaise: r * 100 })}
              >
                {formatPaise(r * 100)}
              </Chip>
            ))}
          </div>
        </>
      )}
      <section className="flex flex-col gap-2 rounded-md bg-surface px-4 py-3.5">
        <strong>{t("onboarding.budget.exTitle")}</strong>
        <p className="text-sm leading-[22px] text-fg-secondary">{t("onboarding.budget.exBody")}</p>
        <div>
          <Chip kind="reason">{t("onboarding.budget.exChip")}</Chip>
        </div>
      </section>
    </OnboardingFrame>
  );
}
