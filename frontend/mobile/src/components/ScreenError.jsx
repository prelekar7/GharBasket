// The subpath, not the barrel: this is in the entry chunk (main.jsx), and the barrel would bring
// the whole UI kit into every first load.
import { ErrorState } from "@gharbasket/ui/states";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

/**
 * "Something went wrong" for a screen, with Retry and a way to get help (handbook 09 §9.10: every
 * error offers retry and a support link).
 * @param {{ onRetry: () => void }} props
 */
export function ScreenError({ onRetry }) {
  const { t } = useTranslation();
  return (
    <ErrorState
      title={t("onboarding.error")}
      retryLabel={t("onboarding.retry")}
      onRetry={onRetry}
      support={
        <Link to="/help" className="font-semibold text-link underline">
          {t("help.link")}
        </Link>
      }
    />
  );
}
