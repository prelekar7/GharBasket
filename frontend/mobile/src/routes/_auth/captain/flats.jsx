import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ComingSoon } from "@/components/RoleFrame.jsx";

/** Placeholder until the captain's flats screen lands in the next PR of this stack. */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/captain/flats")({ component: Screen });

function Screen() {
  const { t } = useTranslation();
  return <ComingSoon screen={t("captain.tabs.flats")} module="M7" />;
}
