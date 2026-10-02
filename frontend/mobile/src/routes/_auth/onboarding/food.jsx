import { Button, Checkbox, Chip, SegmentedControl } from "@gharbasket/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { OnboardingFrame } from "@/components/OnboardingFrame.jsx";
import { nextStep, useOnboardingDraft } from "@/features/onboarding/draft.js";

const CUISINES = ["maharashtrian", "north_indian", "gujarati", "south_indian", "bengali"];
const FASTING = ["shravan_mondays", "ekadashi", "navratri", "tuesdays"];

/** Toggle `code` in `list`. @param {string[]} list @param {string} code @param {boolean} on */
const toggle = (list, code, on) => (on ? [...list, code] : list.filter((c) => c !== code));

/** What does your family eat? Diet, cuisines, Jain and fasting rules (design Hf08). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/onboarding/food")({ component: Food });

function Food() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { household: h, updateHousehold } = useOnboardingDraft();
  const section = "flex flex-col gap-2";
  const label = "text-sm leading-5 font-semibold";

  return (
    <OnboardingFrame
      step={2}
      footer={
        <Button
          variant="primary"
          size="lg"
          block
          onPress={() => navigate({ to: nextStep("/onboarding/food") })}
        >
          {t("onboarding.next")}
        </Button>
      }
    >
      <h1 className="font-display text-[28px] leading-[34px] font-semibold">
        {t("onboarding.food.title")}
      </h1>
      <div className={section}>
        <span className={label}>{t("onboarding.food.diet")}</span>
        <SegmentedControl
          label={t("onboarding.food.diet")}
          value={h.diet}
          onChange={(diet) =>
            updateHousehold({ diet: /** @type {"veg" | "egg" | "nonveg"} */ (diet) })
          }
          options={["veg", "egg", "nonveg"].map((value) => ({
            value,
            label: t(`onboarding.food.diets.${value}`),
          }))}
        />
      </div>
      <div className={section}>
        <span className={label}>{t("onboarding.food.cuisine")}</span>
        <div className="flex flex-wrap gap-2">
          {CUISINES.map((c) => (
            <Chip
              key={c}
              isSelected={h.cuisines.includes(c)}
              onChange={(on) => updateHousehold({ cuisines: toggle(h.cuisines, c, on) })}
            >
              {t(`onboarding.food.cuisines.${c}`)}
            </Chip>
          ))}
        </div>
      </div>
      <div className="flex flex-col">
        <span className={label}>{t("onboarding.food.rules")}</span>
        <Checkbox
          label={t("onboarding.food.jain")}
          description={t("onboarding.food.jainDesc")}
          isSelected={h.jain}
          onChange={(jain) => updateHousehold({ jain })}
        />
        <Checkbox
          label={t("onboarding.food.noOnion")}
          isSelected={h.noOnionSomeDays}
          onChange={(noOnionSomeDays) => updateHousehold({ noOnionSomeDays })}
        />
      </div>
      <div className={section}>
        <span className={label}>{t("onboarding.food.fasting")}</span>
        <div className="flex flex-wrap gap-2">
          {FASTING.map((f) => (
            <Chip
              key={f}
              isSelected={h.fasting.includes(f)}
              onChange={(on) => updateHousehold({ fasting: toggle(h.fasting, f, on) })}
            >
              {t(`onboarding.food.fastList.${f}`)}
            </Chip>
          ))}
        </div>
        <p className="text-sm leading-[22px] text-fg-secondary">{t("onboarding.food.fastNote")}</p>
      </div>
    </OnboardingFrame>
  );
}
