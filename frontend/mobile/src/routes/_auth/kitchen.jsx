import { CalendarBlankIcon, CheckSquareIcon } from "@phosphor-icons/react";
import { createFileRoute, redirect } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { FieldFrame } from "@/components/FieldFrame.jsx";
import { HOME_BY_ROLE, useSession } from "@/lib/session.js";

/** Kitchen partner shell: chilli role bar and the Today and Prep tabs (M8). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/kitchen")({
  beforeLoad: () => {
    const role = useSession.getState().activeRole;
    if (role && role !== "kitchen") throw redirect({ to: HOME_BY_ROLE[role] });
  },
  component: KitchenShell,
});

function KitchenShell() {
  const { t } = useTranslation();
  return (
    <FieldFrame
      userRole="kitchen"
      tabs={[
        { value: "", label: t("kitchen.tabs.today"), Icon: CalendarBlankIcon },
        { value: "prep", label: t("kitchen.tabs.prep"), Icon: CheckSquareIcon },
      ]}
    />
  );
}
