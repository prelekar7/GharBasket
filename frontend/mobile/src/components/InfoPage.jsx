import { IconButton } from "@gharbasket/ui";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import { useRouter } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

/**
 * A plain reading page (Help, Terms, Privacy): back, title, content. Works signed in or out.
 * @param {{ title: string, children: import("react").ReactNode }} props
 */
export function InfoPage({ title, children }) {
  const { t } = useTranslation();
  const router = useRouter();
  return (
    <main className="mx-auto flex min-h-dvh max-w-[480px] flex-col gap-4 bg-bg px-5 pt-[calc(12px+env(safe-area-inset-top))] pb-8 text-fg">
      <header className="-ml-3 flex items-center gap-1">
        <IconButton
          label={t("onboarding.back")}
          variant="quiet"
          onPress={() => router.history.back()}
        >
          <ArrowLeftIcon size={24} />
        </IconButton>
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">{title}</h1>
      </header>
      {children}
    </main>
  );
}
