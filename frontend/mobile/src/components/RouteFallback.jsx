// The subpath, not the barrel: this is in the entry chunk (main.jsx), and the barrel would bring
// the whole UI kit into every first load.
import { ErrorState } from "@gharbasket/ui/states";
import { Link, useRouter } from "@tanstack/react-router";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { ScreenError } from "./ScreenError.jsx";

/**
 * The page around a router-level fallback. It keeps clear of the Android status bar and the
 * gesture bar (the router's own default error text sat under the status bar, out of reach). Inside
 * a role screen (`data-role-frame`, RoleFrame) the frame already leaves room at the top.
 * @param {{ children: import("react").ReactNode }} props
 */
function Page({ children }) {
  return (
    <main className="mx-auto flex min-h-dvh max-w-[480px] flex-col justify-center bg-bg px-5 pt-[calc(12px+env(safe-area-inset-top))] pb-[calc(24px+env(safe-area-inset-bottom))] text-fg in-data-[role-frame]:min-h-0 in-data-[role-frame]:pt-3">
      {children}
    </main>
  );
}

/**
 * Router `defaultErrorComponent`: a route threw while rendering. Same screen as every other
 * load failure (ScreenError: retry and a help link); retry re-runs the route's loaders.
 * @param {{ error: unknown, reset: () => void }} props
 */
export function RouteError({ error, reset }) {
  const router = useRouter();
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <Page>
      <ScreenError
        onRetry={() => {
          reset();
          router.invalidate();
        }}
      />
    </Page>
  );
}

/** Router `defaultNotFoundComponent`: a link that goes nowhere (an old deep link, a typo). */
export function RouteNotFound() {
  const { t } = useTranslation();
  const router = useRouter();
  return (
    <Page>
      <ErrorState
        title={t("router.notFound")}
        body={t("router.notFoundBody")}
        retryLabel={t("router.home")}
        // "/" sends a signed-in person to their role's home and the rest to welcome.
        onRetry={() => router.navigate({ to: "/" })}
        support={
          <Link to="/help" className="font-semibold text-link underline">
            {t("help.link")}
          </Link>
        }
      />
    </Page>
  );
}
