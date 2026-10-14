"use server";

import { revalidatePath } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";
import { fieldMessages } from "@/lib/problem-fields.js";

/** @typedef {{ ok: true } | { ok: false, message: string, fields?: Record<string, string> }} Result */

/**
 * A problem as words for the person, with field errors when the API names them.
 * @param {unknown} e
 * @returns {Result}
 */
function failed(e) {
  if (!(e instanceof ApiProblem)) throw e;
  if (e.code === "community.drive_dates_invalid")
    return {
      ok: false,
      message: "Check the dates: a drive can’t start in the past or end before it starts.",
      fields: { ends_on: "Pick a date on or after the start (and not in the past)" },
    };
  if (e.status === 422)
    return {
      ok: false,
      message: "Some details need another look.",
      fields: fieldMessages(e.errors),
    };
  if (e.status === 403)
    return {
      ok: false,
      message: "You can’t do that here. Hub managers start drives for their own hub.",
    };
  return { ok: false, message: "We couldn’t save that. Nothing was changed; try again." };
}

/** "Diwali drive" + 2026 → "diwali-drive-2026". @param {string} name @param {string} startsOn */
const codeOf = (name, startsOn) =>
  `${name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")}-${startsOn.slice(0, 4)}`;

/**
 * Starts a drive (POST /v1/admin/drives). The name and description go out as the typed text in all three
 * languages until the form takes translations; the multiplier applies to every item category.
 * @param {{ name: string, description: string, starts_on: string, ends_on: string, multiplier: number,
 *   scope: "platform" | "hub", codes: string[] }} drive
 * @param {string} hubId the hub selected in the top bar, for a hub drive
 * @param {string} idempotencyKey one per time the form was opened
 * @returns {Promise<Result>}
 */
export async function createDrive(drive, hubId, idempotencyKey) {
  const name = drive.name.trim();
  const description = drive.description.trim();
  const text = (/** @type {string} */ t) => ({ en: t, hi: t, mr: t });
  try {
    await apiPost(
      "/v1/admin/drives",
      {
        code: name ? codeOf(name, drive.starts_on || "0000") : "",
        name: text(name),
        description: description ? text(description) : null,
        scope: drive.scope,
        hub_id: drive.scope === "hub" ? hubId : null,
        society_id: null,
        codes: drive.codes,
        multiplier: drive.multiplier,
        starts_on: drive.starts_on,
        ends_on: drive.ends_on,
        ngo_id: null,
        goal_items: null,
      },
      { idempotencyKey },
    );
  } catch (e) {
    return failed(e);
  }
  revalidatePath("/drives");
  return { ok: true };
}

/**
 * Adds an NGO (POST /v1/admin/ngos, admin). It starts unverified; its coordinator is a role an admin grants
 * on Users and roles, not something typed here.
 * @param {{ name: string, kind: string, registration_ref: string }} ngo
 * @param {string} hubId the hub it will collect from
 * @param {string} idempotencyKey
 * @returns {Promise<Result>}
 */
export async function addNgo(ngo, hubId, idempotencyKey) {
  try {
    await apiPost(
      "/v1/admin/ngos",
      {
        name: ngo.name.trim(),
        kind: ngo.kind,
        hub_ids: [hubId],
        accepts: ["items", "food", "meals"],
        registration_ref: ngo.registration_ref.trim() || null,
        note: null,
      },
      { idempotencyKey },
    );
  } catch (e) {
    return failed(e);
  }
  revalidatePath("/drives");
  return { ok: true };
}
