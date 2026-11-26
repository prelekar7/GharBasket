import { EmptyState } from "@gharbasket/ui";
import { useTranslation } from "react-i18next";
import { ProfileActions } from "@/components/ProfileActions.jsx";
import { ScreenError } from "@/components/ScreenError.jsx";
import { ApiError } from "@/lib/api.js";

/**
 * A kitchen screen that could not load. `partners.kitchen_not_found` is not a network problem: the
 * person is a verified kitchen partner whose kitchen the hub has not added yet, so it says that
 * instead of "check your internet". Either way the account actions follow, because the kitchen
 * shell keeps Switch role and Sign out on its Earnings tab: when that tab fails too, the person had
 * no way out (phone test, 26 Nov).
 * @param {{ errors: unknown[], onRetry: () => void }} props
 */
export function KitchenError({ errors, onRetry }) {
  const { t } = useTranslation();
  const notSetUp = errors.some(
    (e) => e instanceof ApiError && e.code === "partners.kitchen_not_found",
  );
  return (
    <div className="flex flex-col gap-8">
      {notSetUp ? (
        <EmptyState title={t("kitchen.notSetUp.title")} body={t("kitchen.notSetUp.body")} />
      ) : (
        <ScreenError onRetry={onRetry} />
      )}
      <ProfileActions />
    </div>
  );
}
