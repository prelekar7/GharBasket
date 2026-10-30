"use server";

import { ApiProblem, apiDelete, apiPatch, apiPost } from "@/lib/api.js";
import { describeProblem } from "@/lib/catalog.js";
import { datesBetween, MAX_RUN_DAYS, planFestivalSave } from "@/lib/festivals.js";

/**
 * @typedef {import("@/lib/festivals.js").Festival} Festival
 * @typedef {import("@/lib/festivals.js").FestivalInput} FestivalInput
 * @typedef {{ ok: true, festival: Festival } | { ok: false, message: string, fields: Record<string, string> }} SaveResult
 */

const MESSAGES = {
  "auth.forbidden": "Only an admin can change the calendar.",
  "catalog.festival_not_found": "This day no longer exists. Reload the page.",
  "catalog.festival_exists": "This day is already in the calendar.",
  "catalog.festival_past": "A day that has started cannot be removed.",
  "catalog.names_incomplete": "Give the day a name.",
  "request.idempotency_mismatch": "This save was already sent with other values. Press Save again.",
};

/**
 * Saves a festival or fasting day. The API has one row per day (M2 §3.3), so a run is saved as one call per
 * day: `POST /v1/admin/festivals` for a day that is new, `PATCH /v1/admin/festivals/{day}/{code}/{region}` for
 * a day the run already had (only when something changed), and `DELETE` on the same path for a day the run no
 * longer covers. `planFestivalSave` (lib/festivals.js) works out the calls; the posts and patches go first, so
 * a failure never leaves the run with fewer days than it had. The panel makes the Idempotency-Key once per
 * save and each POST derives its own from it, so pressing Save again after a failure is safe. The first call
 * that fails stops the save with its message; the days before it stay saved.
 * @param {Festival | null} old the run being edited (what the panel showed), or null for a new one
 * @param {FestivalInput} input
 * @param {string} idempotencyKey
 * @returns {Promise<SaveResult>}
 */
export async function saveFestival(old, input, idempotencyKey) {
  const days = datesBetween(input.starts_on, input.ends_on).length;
  if (days > MAX_RUN_DAYS) {
    const message = `A run is at most ${MAX_RUN_DAYS} days. Save a shorter one and add the rest as another.`;
    return { ok: false, message, fields: { ends_on: message } };
  }
  try {
    const plan = planFestivalSave(old, input, idempotencyKey);
    for (const { body, idempotencyKey: key } of plan.posts)
      await apiPost("/v1/admin/festivals", body, { idempotencyKey: key });
    for (const { path, body } of plan.patches) await apiPatch(path, body);
    for (const { path } of plan.deletes) {
      try {
        await apiDelete(path);
      } catch (error) {
        // Already gone (a retry of a save that got this far): that is what we wanted.
        if (!(error instanceof ApiProblem && error.code === "catalog.festival_not_found"))
          throw error;
      }
    }
    return {
      ok: true,
      festival: {
        ...input,
        name: input.name.trim(),
        id: `${input.code}:${input.region}:${input.starts_on}`,
      },
    };
  } catch (error) {
    if (!(error instanceof ApiProblem)) throw error;
    return { ok: false, ...describeProblem(error, MESSAGES) };
  }
}
