import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ComingSoon } from "@/components/RoleFrame.jsx";

/** Shortages arrive in a later change (design Packer · Shortages; M6). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/packer/short")({
  component: () => <ComingSoon screen={useTranslation().t("packer.tabs.short")} module="M6" />,
});
