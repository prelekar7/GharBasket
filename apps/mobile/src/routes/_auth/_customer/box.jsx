import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ComingSoon } from "../../../components/RoleFrame.jsx";

// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/box")({ component: Box });

function Box() {
  const { t } = useTranslation();
  return <ComingSoon screen={t("tabs.box")} module="M3" />;
}
