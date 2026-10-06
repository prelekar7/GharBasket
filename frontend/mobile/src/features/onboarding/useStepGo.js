import { toast } from "@gharbasket/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api.js";
import { nextStep, useOnboardingDraft } from "./draft.js";
import { saveHouseholdEdits } from "./save.js";

/** Saves that fail with their own words; any other failure is "check your internet". */
const ERROR_COPY = /** @type {Record<string, string>} */ ({
  "identity.version_conflict": "profile.edit.conflict",
  "identity.diet_conflict": "onboarding.consent.errors.identity_diet_conflict",
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
      return saveHouseholdEdits(household);
    },
    onSuccess: () => {
      stopEdit();
      client.invalidateQueries({ queryKey: ["household", "me"] });
      client.invalidateQueries({ queryKey: ["consents"] });
      client.invalidateQueries({ queryKey: ["cycle"] });
      toast(t("profile.edit.saved"));
      navigate({ to: "/household" });
    },
    onError: (e) => toast(t((e instanceof ApiError && ERROR_COPY[e.code]) || "profile.saveFailed")),
  });
  return {
    editing: !!editing,
    label: t(editing ? "profile.edit.save" : "onboarding.next"),
    busy: save.isPending,
    go: () => (editing ? save.mutate() : navigate({ to: nextStep(path) })),
  };
}
