import { GullakIcon } from "@gharbasket/ui";
import { InfoIcon, ReceiptIcon, TruckIcon } from "@phosphor-icons/react";
import { createFileRoute, redirect } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { FieldFrame } from "@/components/FieldFrame.jsx";
import { useLocationPings } from "@/features/logistics/location.js";
import { useMyRoute } from "@/features/logistics/queries.js";
import { HOME_BY_ROLE, useSession } from "@/lib/session.js";

/** Rider shell: role bar, outbox banner and the four tabs (designs RiderRoute … RiderEarnings; M7). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/rider")({
  beforeLoad: () => {
    const role = useSession.getState().activeRole;
    if (role && role !== "rider") throw redirect({ to: HOME_BY_ROLE[role] });
  },
  component: RiderShell,
});

function RiderShell() {
  const { t, i18n } = useTranslation();
  const route = useMyRoute(i18n.resolvedLanguage ?? "en-IN").data?.route;
  useLocationPings(route?.id, route?.status === "in_progress");
  return (
    <FieldFrame
      userRole="rider"
      tabs={[
        { value: "", label: t("rider.tabs.route"), Icon: TruckIcon },
        { value: "stop", label: t("rider.tabs.stop"), Icon: InfoIcon },
        { value: "pickups", label: t("rider.tabs.pickups"), Icon: GullakIcon },
        { value: "earnings", label: t("rider.tabs.earnings"), Icon: ReceiptIcon },
      ]}
    />
  );
}
