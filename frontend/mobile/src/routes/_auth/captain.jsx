import { BasketIcon, CalendarBlankIcon, ReceiptIcon, ScanIcon } from "@phosphor-icons/react";
import { createFileRoute, redirect } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { FieldFrame } from "@/components/FieldFrame.jsx";
import { HOME_BY_ROLE, useSession } from "@/lib/session.js";

/** Society Captain shell: Today, Scan, Flats, Earnings (field-role canvas, Captain row; M7, M8). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/captain")({
  beforeLoad: () => {
    const role = useSession.getState().activeRole;
    if (role && role !== "captain") throw redirect({ to: HOME_BY_ROLE[role] });
  },
  component: CaptainShell,
});

function CaptainShell() {
  const { t } = useTranslation();
  return (
    <FieldFrame
      userRole="captain"
      tabs={[
        { value: "", label: t("captain.tabs.today"), Icon: CalendarBlankIcon },
        { value: "scan", label: t("captain.tabs.scan"), Icon: ScanIcon },
        { value: "flats", label: t("captain.tabs.flats"), Icon: BasketIcon },
        { value: "earnings", label: t("captain.tabs.earnings"), Icon: ReceiptIcon },
      ]}
    />
  );
}
