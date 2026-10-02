import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ComingSoon } from "../../../components/RoleFrame.jsx";

// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/kindness")({ component: Kindness });

function Kindness() {
  const { t } = useTranslation();
  return <ComingSoon screen={t("tabs.kindness")} module="M9" />;
}
