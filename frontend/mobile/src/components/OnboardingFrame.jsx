import { IconButton, ProgressBar } from "@gharbasket/ui";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import { useRouter } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { HOUSEHOLD_STEPS } from "@/features/onboarding/draft.js";

/**
 * Layout shared by the onboarding screens (designs Hf04–Hf15): back button, content, and a footer
 * pinned to the bottom.
 * `step` (1–5) adds the "Household · Step N of 5" progress bar of designs Hf07–Hf11.
 * `back={false}` hides the back button on the last screen, where there is nothing to go back to (Hf15).
 * @param {{ children: import("react").ReactNode, footer?: import("react").ReactNode, step?: number, back?: boolean }} props
 */
export function OnboardingFrame({ children, footer, step, back = true }) {
  const { t } = useTranslation();
  const router = useRouter();
  return (
    <main className="mx-auto flex min-h-dvh max-w-[480px] flex-col gap-4 bg-bg px-5 pt-[calc(12px+env(safe-area-inset-top))] pb-6 text-fg">
      {back && (
        <div className="-ml-3 flex min-h-12 items-center">
          <IconButton
            label={t("onboarding.back")}
            variant="quiet"
            onPress={() => router.history.back()}
          >
            <ArrowLeftIcon size={24} />
          </IconButton>
        </div>
      )}
      {step && (
        <ProgressBar
          label={t("onboarding.household")}
          value={step}
          max={HOUSEHOLD_STEPS}
          valueLabel={t("onboarding.stepOf", { step, total: HOUSEHOLD_STEPS })}
        />
      )}
      {children}
      {footer && <div className="mt-auto flex flex-col gap-2 pt-2">{footer}</div>}
    </main>
  );
}
