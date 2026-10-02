import { createFileRoute, redirect } from "@tanstack/react-router";
import { HOME_BY_ROLE, useSession } from "@/lib/session.js";

/** "/" sends people to login, the role chooser, or their role's home. */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/")({
  beforeLoad: async () => {
    if (!(await useSession.getState().restore())) throw redirect({ to: "/login" });
    const { activeRole } = useSession.getState();
    if (!activeRole) throw redirect({ to: "/role" });
    throw redirect({ to: HOME_BY_ROLE[activeRole] });
  },
});
