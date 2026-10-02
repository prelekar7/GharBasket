import { EmptyState } from "@gharbasket/ui";
import { useTranslation } from "react-i18next";
import { ProfileActions } from "./ProfileActions.jsx";
import { RoleFrame } from "./RoleFrame.jsx";

/**
 * Shell for a field role (captain, kitchen, rider, packer). Each role is its own route, so its code
 * is a separate lazy chunk. Real screens are designed at the start of Phase 3 (ADR-0017).
 * @param {{ userRole: "captain" | "kitchen" | "rider" | "packer" }} props
 */
export function FieldShell({ userRole }) {
  const { t } = useTranslation();
  const name = t(`role.${userRole}`);
  return (
    <RoleFrame userRole={userRole}>
      <main className="flex flex-1 flex-col gap-6 px-5 pt-4 pb-8">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">{name}</h1>
        <EmptyState
          title={t("shell.comingTitle", { screen: name })}
          body={t("shell.fieldBody", { role: name })}
        />
        <ProfileActions />
      </main>
    </RoleFrame>
  );
}
