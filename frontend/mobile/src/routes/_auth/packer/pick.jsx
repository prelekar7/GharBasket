import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ComingSoon } from "@/components/RoleFrame.jsx";

/** Pick and pack arrives in the next change (design Packer · Pick and pack; M6). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/packer/pick")({
  validateSearch: (/** @type {Record<string, unknown>} */ s) => ({
    bag: typeof s.bag === "string" ? s.bag : undefined,
  }),
  component: () => <ComingSoon screen={useTranslation().t("packer.tabs.pick")} module="M6" />,
});
