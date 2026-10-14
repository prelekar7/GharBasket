import { ReceiptIcon, ScanIcon, WarningIcon } from "@phosphor-icons/react";
import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { FieldFrame } from "@/components/FieldFrame.jsx";

/** Packer shell: Queue · Pick/Pack · Shortages, scanner first (Handbook 09 §9.7, M6). */
// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/packer")({ component: PackerShell });

function PackerShell() {
  const { t } = useTranslation();
  return (
    <FieldFrame
      userRole="packer"
      tabs={[
        { value: "", label: t("packer.tabs.queue"), Icon: ReceiptIcon },
        { value: "pick", label: t("packer.tabs.pick"), Icon: ScanIcon },
        { value: "short", label: t("packer.tabs.short"), Icon: WarningIcon },
      ]}
    />
  );
}
