import { IconButton } from "@gharbasket/ui";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import { useRouter } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

/**
 * Layout shared by the onboarding screens (designs Hf04–Hf15): back button, content, and a footer
 * pinned to the bottom.
 * @param {{ children: import("react").ReactNode, footer?: import("react").ReactNode }} props
 */
export function OnboardingFrame({ children, footer }) {
  const { t } = useTranslation();
  const router = useRouter();
  return (
    <main className="mx-auto flex min-h-dvh max-w-[480px] flex-col gap-4 bg-bg px-5 pt-[calc(12px+env(safe-area-inset-top))] pb-6 text-fg">
      <div className="-ml-3 flex min-h-12 items-center">
        <IconButton
          label={t("onboarding.back")}
          variant="quiet"
          onPress={() => router.history.back()}
        >
          <ArrowLeftIcon size={24} />
        </IconButton>
      </div>
      {children}
      {footer && <div className="mt-auto flex flex-col gap-1 pt-2">{footer}</div>}
    </main>
  );
}
