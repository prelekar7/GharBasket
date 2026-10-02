import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";
import { useSession } from "@/lib/session.js";

/** Pathless guard for every signed-in screen. The server still checks permissions on each request. */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth")({
  beforeLoad: async () => {
    if (!(await useSession.getState().restore())) throw redirect({ to: "/login" });
    const { activeRole } = useSession.getState();
    if (!activeRole) throw redirect({ to: "/role" });
  },
  component: Outlet,
});
