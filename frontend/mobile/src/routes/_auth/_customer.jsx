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
import { householdQuery } from "@/features/identity/profile.js";
import { resumeStep } from "@/features/onboarding/draft.js";
import { queryClient } from "@/lib/queryClient.js";
import { HOME_BY_ROLE, useSession } from "@/lib/session.js";

/** How long the customer shell waits for GET /v1/households/me before opening the app anyway. */
const HOUSEHOLD_CHECK_MS = 4000;

/** Customer shell: role bar, the five tabs and the floating Ghar Saathi button (FDD §8). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer")({
  beforeLoad: async () => {
    const role = useSession.getState().activeRole;
    if (role && role !== "customer") throw redirect({ to: HOME_BY_ROLE[role] });
    // No household yet (onboarding left halfway): finish setting it up first, where they left
    // off. Offline, an error or a slow network never blocks the app: after HOUSEHOLD_CHECK_MS we
    // let them in.
    const household = await Promise.race([
      queryClient.ensureQueryData(householdQuery).catch(() => undefined),
      new Promise((resolve) => setTimeout(resolve, HOUSEHOLD_CHECK_MS, undefined)),
    ]);
    if (household === null) {
      queryClient.removeQueries({ queryKey: householdQuery.queryKey });
      throw redirect({ to: resumeStep() });
    }
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

const UNDER_PROFILE = ["wallet", "society", "partner"];

function CustomerShell() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const senior = useSession((s) => !!s.user?.senior_mode);
  const segment = pathname.split("/")[1] || "week";
  // Wallet, Society and Become a partner open from Profile (HcWallet, HcSociety, HcPartner).
  const current = UNDER_PROFILE.includes(segment) ? "profile" : segment;
  // The designs leave the assistant button off Profile and Become a partner.
  const showAssistant = segment !== "profile" && segment !== "partner";
  return (
    <RoleFrame userRole="customer">
      <main className="relative flex-1 px-5 pt-4 pb-24">
        <InstallBanner />
        <Outlet />
      </main>
      {showAssistant && (
        <button
          type="button"
          aria-label={t("assistant")}
          onClick={() => navigate({ to: "/assistant" })}
          className="fixed right-4 bottom-[calc(96px+env(safe-area-inset-bottom))] grid size-14 place-items-center rounded-full bg-primary text-on-primary shadow-sheet outline-none focus-visible:shadow-focus"
        >
          <ChatCircleTextIcon size={26} />
        </button>
      )}
      <div className="fixed inset-x-0 bottom-0">
        <TabBar
          label={t("tabs.main")}
          senior={senior}
          value={current}
          onChange={(value) => navigate({ to: `/${value}` })}
          items={TABS.map(({ value, Icon }) => ({
            value,
            label: t(`tabs.${value}`),
            icon: <Icon size={senior ? 28 : 24} />,
          }))}
        />
      </div>
    </RoleFrame>
  );
}
