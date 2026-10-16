import { Button, GullakMeter, toast } from "@gharbasket/ui";
import { useTranslation } from "react-i18next";
import { useDonateMeal } from "./queries.js";

/**
 * Kindness points as the gullak meter, with "Donate a meal" once they reach the goal: donated meals
 * are the only way to use points (M5 §3.6). Without a goal (KINDNESS_GOAL unset) it shows the points only.
 * @param {{ balance: import("./queries.js").KindnessBalance, canDonate?: boolean }} props
 */
export function KindnessGullak({ balance, canDonate = false }) {
  const { t } = useTranslation();
  const donate = useDonateMeal();
  const labels = {
    title: t("wallet.kindness"),
    progress: t("wallet.kindnessProgress"),
    remaining: t("wallet.kindnessRemaining"),
  };
  const goal = balance.goal;
  if (!goal)
    return (
      <section className="flex flex-col gap-1 rounded-md bg-surface px-4 py-3.5">
        <span className="text-xs leading-[18px] font-semibold tracking-[0.04em] text-fg-secondary uppercase">
          {labels.title}
        </span>
        <span className="font-display text-[36px] leading-10 font-medium tabular-nums">
          {balance.points}
        </span>
      </section>
    );
  return (
    <>
      <GullakMeter
        points={balance.points}
        goal={goal.points}
        label={t(`wallet.goals.${goal.code}`, { defaultValue: goal.code })}
        labels={labels}
      />
      {canDonate && balance.points >= goal.points && (
        <Button
          variant="secondary"
          block
          isPending={donate.isPending}
          onPress={() =>
            donate.mutate(1, {
              onSuccess: () => toast(t("kindness.mealGiven")),
              onError: () => toast(t("kindness.mealFailed")),
            })
          }
        >
          {t("kindness.donateMeal")}
        </Button>
      )}
    </>
  );
}
