import { EmptyState } from "@gharbasket/ui";
import { useTranslation } from "react-i18next";
import { OutboxBanner } from "./OutboxBanner.jsx";

/** @type {Record<import("../lib/api.js").FieldRole, string>} */
const BAR = {
  customer: "bg-role-customer",
  captain: "bg-role-captain",
  kitchen: "bg-role-kitchen",
  rider: "bg-role-rider",
  packer: "bg-role-packer",
};

/**
 * Every role screen starts with the role bar, so a person with two roles always knows the mode.
 * Field roles use a thicker 6px bar, readable at arm's length (field-role canvas).
 * @param {{ userRole: import("../lib/api.js").FieldRole, thick?: boolean, children: import("react").ReactNode }} props
 */
export function RoleFrame({ userRole, thick = false, children }) {
  return (
    <div className="flex min-h-dvh flex-col bg-bg pt-[env(safe-area-inset-top)] text-fg">
      <div aria-hidden className={`${thick ? "h-1.5" : "h-[3px]"} shrink-0 ${BAR[userRole]}`} />
      <OutboxBanner />
      {children}
    </div>
  );
}

/**
 * Placeholder for a screen whose module has not landed yet. Says which module brings it.
 * @param {{ screen: string, module: string }} props
 */
export function ComingSoon({ screen, module }) {
  const { t } = useTranslation();
  return (
    <EmptyState
      title={t("shell.comingTitle", { screen })}
      body={t("shell.comingBody", { module })}
    />
  );
}
