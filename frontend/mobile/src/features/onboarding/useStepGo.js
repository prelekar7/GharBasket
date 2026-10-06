import { toast } from "@gharbasket/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ApiError, request } from "@/lib/api.js";
import { nextStep, useOnboardingDraft } from "./draft.js";

/** The household as PATCH /v1/households/{id} takes it (M1, If-Match). @param {import("./draft.js").HouseholdDraft} h */
const toApi = (h) => ({
  members: h.members,
  diet: h.diet,
  cuisine_tags: h.cuisines,
  rules: { jain: h.jain, no_onion_some_days: h.noOnionSomeDays },
  fasting: h.fasting,
  health_consent: h.healthConsent,
  health: h.healthConsent ? h.health : {},
  weekday_cook_minutes: h.weekdayCookMinutes,
  weekend_cook_minutes: h.weekendCookMinutes,
  no_cook_days: h.noCookDays,
  budget_paise: h.budgetLockPaise,
});

/**
 * The main button of a household step (Hf07–Hf11). During onboarding it is Next; when Profile is
 * editing a saved household it is Save, which sends the change and goes back to Household and food.
 * @param {string} path this step's route, for the onboarding order
 */
export function useStepGo(path) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const { editing, household, stopEdit } = useOnboardingDraft();
  const save = useMutation({
    mutationFn: () => {
      if (!editing) throw new Error("not editing");
      return request(`/v1/households/${editing.id}`, {
        method: "PATCH",
        body: toApi(household),
        headers: { "if-match": String(editing.version) },
      });
    },
    onSuccess: () => {
      stopEdit();
      client.invalidateQueries({ queryKey: ["household", "me"] });
      client.invalidateQueries({ queryKey: ["cycle", "current"] });
      toast(t("profile.edit.saved"));
      navigate({ to: "/household" });
    },
    onError: (e) =>
      toast(
        t(
          e instanceof ApiError && e.code === "identity.version_conflict"
            ? "profile.edit.conflict"
            : "profile.saveFailed",
        ),
      ),
  });
  return {
    editing: !!editing,
    label: t(editing ? "profile.edit.save" : "onboarding.next"),
    busy: save.isPending,
    go: () => (editing ? save.mutate() : navigate({ to: nextStep(path) })),
  };
}
