"use server";

import { revalidatePath } from "next/cache";
import { ApiProblem, apiGet, apiPost } from "@/lib/api.js";
import { humanMessage } from "@/lib/ops-errors.js";
import { amountRefusal, RESOLVE_MESSAGES } from "@/lib/order-issues.js";

/**
 * One issue with fresh photo links (5 minutes) and the customer's contact (`GET
 * /v1/admin/order-issues/{id}`; every call is audit-logged). The sheet asks again each time it opens.
 * @param {string} id
 * @returns {Promise<{ ok: true, issue: any } | { ok: false, message: string }>}
 */
export async function loadIssue(id) {
  try {
    return { ok: true, issue: await apiGet(`/v1/admin/order-issues/${encodeURIComponent(id)}`) };
  } catch (error) {
    return { ok: false, message: humanMessage(error, RESOLVE_MESSAGES) };
  }
}

/**
 * The order's timeline, as JSON (`GET /v1/orders/{id}/events`); the sheet polls it.
 * @param {string} orderId
 * @returns {Promise<{ ok: true, status: string, items: any[] } | { ok: false }>}
 */
export async function loadTimeline(orderId) {
  try {
    const t = await apiGet(`/v1/orders/${encodeURIComponent(orderId)}/events`);
    return { ok: true, status: t.status, items: t.items };
  } catch {
    return { ok: false };
  }
}

/**
 * Credits an open issue to the wallet, or rejects it (`POST .../resolve`, Idempotency-Key).
 * @param {string} id
 * @param {{ outcome: "credit" | "reject", credit_paise?: number, note?: string }} body
 * @param {string} idempotencyKey one per form state: a new key whenever the body changes
 * @returns {Promise<{ ok: true, issue: any } | { ok: false, message: string, settled: boolean }>}
 *   `settled`: someone else already resolved it, so the sheet should show the issue as it is now
 */
export async function resolveIssue(id, body, idempotencyKey) {
  try {
    const issue = await apiPost(`/v1/admin/order-issues/${encodeURIComponent(id)}/resolve`, body, {
      idempotencyKey,
    });
    revalidatePath("/issues");
    return { ok: true, issue };
  } catch (error) {
    const refusal = error instanceof ApiProblem ? amountRefusal(error.code, error.extra) : null;
    const settled = error instanceof ApiProblem && error.code === "orders.issue_not_open";
    if (settled) revalidatePath("/issues");
    return { ok: false, message: refusal ?? humanMessage(error, RESOLVE_MESSAGES), settled };
  }
}
