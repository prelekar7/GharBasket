"use server";

import { refresh } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { fieldErrors, humanMessage } from "@/lib/ops-errors.js";

/**
 * Adds a vehicle to the selected hub (`POST /v1/hubs/{id}/vehicles`, M7 §3.1). The caller makes one Idempotency-Key per
 * attempt and reuses it on retry. Field problems come back by field name, so the form can show each next to its input.
 * @param {string} idempotencyKey
 * @param {{ label: string, kind: string, registration?: string, capacity_kg?: number, capacity_crates?: number,
 *   capacity_tiffins?: number, default_rider_user_id?: string }} input
 * @returns {Promise<{ ok: true, label: string } | { ok: false, message: string, fields: Record<string, string> }>}
 */
export async function addVehicle(idempotencyKey, input) {
  const hub = await currentHub();
  try {
    const vehicle = await apiPost(`/v1/hubs/${encodeURIComponent(hub.id)}/vehicles`, input, {
      idempotencyKey,
    });
    refresh();
    return { ok: true, label: vehicle.label };
  } catch (e) {
    const fields = fieldErrors(e);
    const code = e instanceof ApiProblem ? e.code : "";
    if (code === "logistics.vehicle_label_taken")
      fields.label = "This label is already on the list.";
    if (code === "logistics.rider_invalid") {
      fields.default_rider_user_id = "That person is not an active rider of this hub.";
    }
    return {
      ok: false,
      fields,
      message: Object.keys(fields).length ? "" : humanMessage(e),
    };
  }
}
