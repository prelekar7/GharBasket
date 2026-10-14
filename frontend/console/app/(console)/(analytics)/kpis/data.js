import { apiGet } from "@/lib/api.js";
import { getRole } from "@/lib/auth.js";
import { apiSource, parseSource } from "@/lib/data-source.js";
import { currentHub, listHubs } from "@/lib/hub.js";

/** The windows the Period select offers, in weeks. */
export const PERIODS = /** @type {const} */ ({ "4w": 4, "12w": 12 });

/** The Hub select's value for every hub added together. Admins only. */
export const ALL_HUBS = "all";

/**
 * GET /v1/analytics/kpis?hub_id=&from=&to=&grain=week&data_source= for the last 4 or 12 weeks. `hub_id` is one
 * hub the person may see; leaving it out asks for every hub (M12 section 3.3, admins only). The data source
 * is required and is never mixed: the Data source control picks `synthetic` (simulator) or `real` (pilot). The Hub select offers
 * "All hubs" to admins and otherwise only the hubs from `GET /v1/hubs`, which are already scoped to the
 * person (a franchise owner or hub manager gets theirs). A `hub` the person may not pick falls back to
 * the hub chosen in the top bar.
 * @param {string | undefined} period "4w" (default) or "12w"
 * @param {string | undefined} hub a hub id or "all" (from the URL)
 * @param {string | undefined} source "simulator" (default) or "pilot" (from the URL)
 * @returns {Promise<import("./tiles.js").Kpis & { period: keyof typeof PERIODS, hub: string, hubs: { id: string, name: string }[] }>}
 */
export async function loadKpis(period, hub, source) {
  const key = period === "12w" ? "12w" : "4w";
  const [role, hubs, selected] = await Promise.all([getRole(), listHubs(), currentHub()]);
  const options = [...(role === "admin" ? [{ id: ALL_HUBS, name: "All hubs" }] : []), ...hubs];
  const chosen = options.find((h) => h.id === hub) ?? options.find((h) => h.id === selected.id);
  const hubId = chosen?.id ?? selected.id;
  const day = 86_400_000;
  const ist = 5.5 * 3_600_000; // the API takes IST dates
  const now = Date.now() + ist; // read after the request's data, so the page stays dynamic
  const data = await apiGet("/v1/analytics/kpis", {
    searchParams: {
      hub_id: hubId === ALL_HUBS ? undefined : hubId,
      from: new Date(now - PERIODS[key] * 7 * day).toISOString().slice(0, 10),
      to: new Date(now).toISOString().slice(0, 10),
      grain: "week",
      data_source: apiSource(parseSource(source)),
    },
  });
  return { ...data, period: key, hub: hubId, hubs: options };
}
