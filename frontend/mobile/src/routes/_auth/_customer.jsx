import { TabBar } from "@gharbasket/ui";
import {
  BasketIcon,
  CalendarBlankIcon,
  ChatCircleTextIcon,
  HeartIcon,
  ReceiptIcon,
  UserIcon,
} from "@phosphor-icons/react";
import {
  createFileRoute,
  Outlet,
  redirect,
  useLocation,
  useNavigate,
} from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { InstallBanner } from "@/components/InstallBanner.jsx";
import { RoleFrame } from "@/components/RoleFrame.jsx";
import { HOME_BY_ROLE, useSession } from "@/lib/session.js";

/** Customer shell: role bar, the five tabs and the floating Ghar Saathi button (FDD §8). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer")({
  beforeLoad: () => {
    const role = useSession.getState().activeRole;
    if (role && role !== "customer") throw redirect({ to: HOME_BY_ROLE[role] });
  },
  component: CustomerShell,
});

const TABS = [
  { value: "week", Icon: CalendarBlankIcon },
  { value: "box", Icon: BasketIcon },
  { value: "orders", Icon: ReceiptIcon },
  { value: "kindness", Icon: HeartIcon },
  { value: "profile", Icon: UserIcon },
];

function CustomerShell() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  // Wallet lives under Profile in the design (HcWallet).
  const segment = pathname.split("/")[1] || "week";
  const current = segment === "wallet" ? "profile" : segment;
  return (
    <RoleFrame userRole="customer">
      <main className="relative flex-1 px-5 pt-4 pb-24">
        <InstallBanner />
        <Outlet />
      </main>
      <button
        type="button"
        aria-label={t("assistant")}
        className="fixed right-4 bottom-[calc(96px+env(safe-area-inset-bottom))] grid size-14 place-items-center rounded-full bg-primary text-on-primary shadow-sheet outline-none focus-visible:shadow-focus"
      >
        <ChatCircleTextIcon size={26} />
      </button>
      <div className="fixed inset-x-0 bottom-0">
        <TabBar
          label={t("tabs.main")}
          value={current}
          onChange={(value) => navigate({ to: `/${value}` })}
          items={TABS.map(({ value, Icon }) => ({
            value,
            label: t(`tabs.${value}`),
            icon: <Icon size={24} />,
          }))}
        />
      </div>
    </RoleFrame>
  );
}
