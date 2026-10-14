import { Button } from "@gharbasket/ui";
import { WalletIcon } from "@phosphor-icons/react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ProfileActions } from "@/components/ProfileActions.jsx";

// @ts-expect-error route paths are untyped in JavaScript (router plugin disableTypes)
export const Route = createFileRoute("/_auth/_customer/profile")({ component: Profile });

function Profile() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <div className="flex flex-col gap-6">
      <h1 className="font-display text-[28px] leading-[34px] font-semibold">{t("tabs.profile")}</h1>
      <Button variant="secondary" block onPress={() => navigate({ to: "/wallet" })}>
        <WalletIcon aria-hidden size={20} />
        {t("wallet.title")}
      </Button>
      <ProfileActions />
    </div>
  );
}
