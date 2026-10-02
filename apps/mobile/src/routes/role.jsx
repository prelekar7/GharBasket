import { Button } from "@gharbasket/ui";
import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { HOME_BY_ROLE, useSession } from "../lib/session.js";

/** Role chooser: shown when a person holds more than one verified role (handbook ch. 03 §2). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/role")({
  beforeLoad: () => {
    if (!useSession.getState().accessToken) throw redirect({ to: "/login" });
  },
  component: RoleChooser,
});

function RoleChooser() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { roles, setActiveRole } = useSession();
  return (
    <main className="mx-auto flex min-h-dvh max-w-[480px] flex-col gap-4 px-5 pt-[calc(48px+env(safe-area-inset-top))]">
      <h1 className="font-display text-[28px] leading-[34px] font-semibold">{t("role.title")}</h1>
      {roles.map((role) => (
        <Button
          key={role}
          size="lg"
          block
          onPress={() => {
            setActiveRole(role);
            navigate({ to: HOME_BY_ROLE[role] });
          }}
        >
          {t(`role.${role}`)}
        </Button>
      ))}
    </main>
  );
}
