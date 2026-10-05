import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";

/** The windows the Period select offers, in weeks. */
export const PERIODS = /** @type {const} */ ({ "4w": 4, "12w": 12 });

/**
 * GET /v1/analytics/kpis?hub_id=&from=&to= for the last 4 or 12 weeks.
 * @param {string | undefined} period "4w" (default) or "12w"
 * @returns {Promise<import("./tiles.js").Kpis & { period: keyof typeof PERIODS }>}
 */
export async function loadKpis(period) {
  const key = period === "12w" ? "12w" : "4w";
  const hub = await currentHub();
  const day = 86_400_000;
  const now = Date.now(); // read after the request's data, so the page stays dynamic
  const data = await apiGet("/v1/analytics/kpis", {
    searchParams: {
      hub_id: hub.id,
      from: new Date(now - PERIODS[key] * 7 * day).toISOString().slice(0, 10),
      to: new Date(now).toISOString().slice(0, 10),
    },
  });
  return { ...data, period: key };
}
