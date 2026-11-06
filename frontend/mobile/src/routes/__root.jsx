import { createRootRoute, Outlet } from "@tanstack/react-router";
import { lazy, Suspense, useEffect } from "react";
import { useSession } from "@/lib/session.js";

// The UI kit (React Aria, drawers, toasts) loads with the first screen, not in the entry chunk.
const Toaster = lazy(() => import("@gharbasket/ui").then((m) => ({ default: m.Toaster })));

export const Route = createRootRoute({
  component: () => (
    <>
      <SeniorMode />
      <Outlet />
      <Suspense fallback={null}>
        <Toaster />
      </Suspense>
    </>
  ),
});

/** Bigger text everywhere while the person has senior mode on (M1 `senior_mode`, see app.css). */
function SeniorMode() {
  const senior = useSession((s) => !!s.user?.senior_mode);
  useEffect(() => {
    document.documentElement.toggleAttribute("data-senior", senior);
  }, [senior]);
  return null;
}
