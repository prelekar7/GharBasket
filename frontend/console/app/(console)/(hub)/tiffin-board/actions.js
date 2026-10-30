"use server";

import { revalidatePath } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { humanMessage } from "@/lib/ops-errors.js";

/** What the person is told for each refusal; the `code` decides, never the server's text. @type {Record<string, string>} */
const MESSAGES = {
  "partners.assignment_closed":
    "Assigning by hand has closed for this meal. It stops 2 hours before the kitchen must have the tiffins ready; tiffins still without a kitchen after that are refunded.",
  "partners.order_not_assignable":
    "Some of those tiffins can't be given to a kitchen: they are not locked tiffins of this day and meal, or they already have a kitchen. Reload the board.",
  "partners.kitchen_not_found": "That kitchen is not an active or paused kitchen of this hub.",
  "partners.assignment_busy":
    "Another change to this hub's tiffins is in progress. Try again in a moment.",
  "request.invalid": "Pick the tiffins and the kitchen, and check the date.",
};

/**
 * @template T
 * @param {() => Promise<T>} run
 * @returns {Promise<{ ok: true, data: T } | { ok: false, message: string }>}
 */
async function attempt(run) {
  try {
    const data = await run();
    revalidatePath("/tiffin-board");
    return { ok: true, data };
  } catch (e) {
    if (e instanceof ApiProblem) return { ok: false, message: humanMessage(e, MESSAGES) };
    throw e;
  }
}

/**
 * Runs one assignment round now for a date (`POST /v1/hubs/{id}/tiffin-assignments/run`). Safe to repeat:
 * a round only offers tiffins that have no live assignment.
 * @param {string} date @param {string} idempotencyKey one per click
 * @returns {Promise<{ ok: true, data: { offers: number, unassigned: number } } | { ok: false, message: string }>}
 */
export async function runAssignment(date, idempotencyKey) {
  const hub = await currentHub();
  return attempt(() =>
    apiPost(`/v1/hubs/${hub.id}/tiffin-assignments/run`, { date }, { idempotencyKey }),
  );
}

/**
 * Gives tiffins of one meal to a kitchen the manager agreed with by phone; accepted at once and it may
 * go over her capacity (`POST /v1/hubs/{id}/tiffin-assignments/manual`).
 * @param {{ date: string, order_ids: string[], partner_id: string, note: string }} input @param {string} idempotencyKey
 * @returns {Promise<{ ok: true, data: { over_capacity: boolean, portions: number } } | { ok: false, message: string }>}
 */
export async function assignByHand(input, idempotencyKey) {
  const hub = await currentHub();
  return attempt(() =>
    apiPost(
      `/v1/hubs/${hub.id}/tiffin-assignments/manual`,
      { ...input, note: input.note.trim() || null },
      { idempotencyKey },
    ),
  );
}
