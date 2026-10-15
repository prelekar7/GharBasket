"use server";

import { refresh } from "next/cache";
import { ApiProblem, apiFirstEvent, apiGet, apiPost } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { humanMessage } from "@/lib/ops-errors.js";

/**
 * The van's latest position and the stops' state, polled by the live map every 15 seconds. `GET /v1/routes/{id}/live`
 * is a server-sent stream whose first event is a snapshot; the console reads that one and closes the stream. Returns null
 * when it could not be read, so the map keeps what it had.
 * @param {string} routeId
 * @returns {Promise<{ position: import("./data.js").VanPosition | null, stops: { seq: number, status: string, eta_now: string | null }[] } | null>}
 */
export async function liveSnapshot(routeId) {
  try {
    const snapshot = await apiFirstEvent(
      `/v1/routes/${encodeURIComponent(routeId)}/live`,
      "snapshot",
    );
    return { position: snapshot.position ?? null, stops: snapshot.stops ?? [] };
  } catch {
    return null;
  }
}

/**
 * The status of a plan being solved (`GET /v1/route-plans/{id}`), polled until it is solved or failed.
 * @param {string} planId
 * @returns {Promise<string | null>}
 */
export async function planStatus(planId) {
  try {
    const plan = await apiGet(`/v1/route-plans/${encodeURIComponent(planId)}`);
    return plan.status;
  } catch {
    return null;
  }
}

/** @typedef {{ ok: true } | { ok: false, message: string }} Result */

const MESSAGES = {
  "logistics.solve_running":
    "A solve is already running for this day. It will show here when it is done.",
  "logistics.plan_has_problems": "This plan has problems. Publish it anyway to accept them.",
  "logistics.plan_incomplete":
    "This plan leaves a stop off every route, so it cannot be published. Solve again.",
  "logistics.routes_started":
    "A route of the published plan has already started, so it cannot be replaced.",
  "logistics.date_out_of_range": "Plans can be solved for today or tomorrow only.",
  "logistics.plan_not_found": "That plan is gone. The page is up to date now.",
};

/**
 * Starts a solve for the day (`POST /v1/hubs/{id}/route-plans`, M7 §3.2; hub manager): the API answers 202 at once and
 * solves in the background, so the page polls the plan.
 * @param {string} idempotencyKey one per click
 * @param {string} date IST date, today or tomorrow
 * @returns {Promise<Result>}
 */
export async function solvePlan(idempotencyKey, date) {
  const hub = await currentHub();
  try {
    await apiPost(
      `/v1/hubs/${encodeURIComponent(hub.id)}/route-plans`,
      { date, kind: "box" },
      { idempotencyKey },
    );
    refresh();
    return { ok: true };
  } catch (e) {
    if (e instanceof ApiProblem && e.code === "logistics.solve_running") refresh();
    return { ok: false, message: humanMessage(e, MESSAGES) };
  }
}

/**
 * Publishes a solved plan (`POST /v1/route-plans/{id}/publish`); with `acceptProblems` the manager takes the plan's
 * listed problems on (recorded in the audit log).
 * @param {string} idempotencyKey one per click
 * @param {string} planId
 * @param {boolean} acceptProblems
 * @returns {Promise<Result>}
 */
export async function publishPlan(idempotencyKey, planId, acceptProblems) {
  try {
    await apiPost(
      `/v1/route-plans/${encodeURIComponent(planId)}/publish`,
      acceptProblems ? { accept_problems: true } : {},
      { idempotencyKey },
    );
    refresh();
    return { ok: true };
  } catch (e) {
    return { ok: false, message: humanMessage(e, MESSAGES) };
  }
}
