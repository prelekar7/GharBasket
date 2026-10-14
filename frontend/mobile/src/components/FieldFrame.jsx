import { IconButton, TabBar } from "@gharbasket/ui";
import { ArrowLeftIcon } from "@phosphor-icons/react";
import { Outlet, useLocation, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useSession } from "@/lib/session.js";
import { RoleFrame } from "./RoleFrame.jsx";

/**
 * @typedef {object} FieldTab
 * @property {string} value Path segment after the role ("" for the role's home).
 * @property {string} label Translated label.
 * @property {import("react").ComponentType<{ size?: number }>} Icon
 */

/**
 * Shell for a field role (packer, rider, captain, kitchen): the thick role bar, the outbox banner,
 * the screen and the role's bottom tabs. Field roles are tools, not apps: one-handed, scan first,
 * and they keep working offline (field-role canvas, Handbook 09).
 * @param {{ userRole: "captain" | "kitchen" | "rider" | "packer", tabs: FieldTab[] }} props
 */
export function FieldFrame({ userRole, tabs }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const senior = useSession((s) => !!s.user?.senior_mode);
  const segment = pathname.split("/")[2] ?? "";
  const current = tabs.some((tab) => tab.value === segment) ? segment : "";
  return (
    <RoleFrame userRole={userRole} thick>
      <main className="flex flex-1 flex-col gap-3 px-4 pt-3 pb-28">
        <Outlet />
      </main>
      <div className="fixed inset-x-0 bottom-0">
        <TabBar
          label={t("tabs.main")}
          role={userRole}
          senior={senior}
          value={current}
          onChange={(value) => navigate({ to: value ? `/${userRole}/${value}` : `/${userRole}` })}
          items={tabs.map(({ value, label, Icon }) => ({
            value,
            label,
            icon: <Icon size={senior ? 28 : 24} />,
          }))}
        />
      </div>
    </RoleFrame>
  );
}

/**
 * Top of a field screen: a small "when and where" line over a bold title, with an optional badge
 * or back button (PackerQueue, CaptainManual).
 * @param {{ eyebrow?: string, title: string, back?: { label: string, onPress: () => void }, end?: import("react").ReactNode }} props
 */
export function FieldHeader({ eyebrow, title, back, end }) {
  return (
    <header className={`flex items-center justify-between gap-2 ${back ? "-ml-2" : ""}`}>
      <div className="flex items-center gap-1">
        {back && <BackButton {...back} />}
        <div className="flex flex-col">
          {eyebrow && (
            <span className="text-[15px] leading-[22px] text-fg-secondary">{eyebrow}</span>
          )}
          <h1 className="m-0 text-2xl leading-[30px] font-bold">{title}</h1>
        </div>
      </div>
      {end}
    </header>
  );
}

/** @param {{ label: string, onPress: () => void }} props */
function BackButton({ label, onPress }) {
  return (
    <IconButton label={label} variant="quiet" onPress={onPress}>
      <ArrowLeftIcon size={24} />
    </IconButton>
  );
}
