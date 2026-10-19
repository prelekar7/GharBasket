import { Toaster } from "@gharbasket/ui";
import { createRootRoute, Outlet } from "@tanstack/react-router";
import { useEffect } from "react";
import { useSession } from "@/lib/session.js";

export const Route = createRootRoute({
  component: () => (
    <>
      <SeniorMode />
      <Outlet />
      <Toaster />
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
