"use server";

import { refresh } from "next/cache";
import { ApiProblem, apiPost } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/**
 * Adds a vehicle to the selected hub. The caller makes one Idempotency-Key per attempt and reuses it on retry.
 * Field problems come back by field name, so the form can show each next to its input.
 * @param {string} idempotencyKey
 * @param {{ kind: string, registration: string, capacity_kg: number, capacity_crates: number }} input
 * @returns {Promise<{ ok: true, name: string } | { ok: false, message: string, fields: Record<string, string> }>}
 */
export async function addVehicle(idempotencyKey, input) {
  const hub = await currentHub();
  try {
    const vehicle = await apiPost(`/v1/hubs/${hub.id}/vehicles`, input, { idempotencyKey });
    refresh();
    return { ok: true, name: vehicle.name };
  } catch (e) {
    if (e instanceof ApiProblem && e.code === "request.invalid") {
      return {
        ok: false,
        message: "Check the highlighted fields.",
        fields: Object.fromEntries(e.errors.map((x) => [x.field, x.message])),
      };
    }
    if (e instanceof ApiProblem && e.code === "logistics.vehicle_exists") {
      return {
        ok: false,
        message: "",
        fields: { registration: "This registration is already on the list." },
      };
    }
    return {
      ok: false,
      message: "We could not add the vehicle. Nothing was saved; try again.",
      fields: {},
    };
  }
}
