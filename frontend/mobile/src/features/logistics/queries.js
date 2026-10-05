import { useQuery, useQueryClient } from "@tanstack/react-query";
import { todayIst } from "@/features/subscriptions/format.js";
import { request } from "@/lib/api.js";
import { enqueue } from "@/lib/outbox.js";

// Rider data (M7 §3). The spec lists endpoints, not fields; these shapes are our proposal.
// Reads are cached and persisted, so a route loaded at the hub keeps working without signal.
// Writes go through the offline outbox and patch the cached route at once (optimistic).

/**
 * @typedef {{ id: string, bags: number, state: "scanned" | "waiting" | "missing" }} RouteCrate
 * @typedef {{ id: string, name: string, gate: string | null, where: string, lat: number, lng: number }} StopSociety
 * @typedef {{ id: string, seq: number, society: StopSociety, captain: { name: string, phone: string } | null,
 *   eta: string, bags_count: number, crates: RouteCrate[], status: "todo" | "done" }} RouteStop
 * @typedef {{ id: string, kind: "crate" | "donation", stop_id: string, qty: number, ref: string | null,
 *   codes: string[], note: string, status: "pending" | "collected" }} Pickup
 * @typedef {{ id: string, date: string, slot: { id: string, from: string, to: string }, vehicle: { id: string, plate: string },
 *   hub: { id: string, name: string, lat: number, lng: number }, status: "published" | "started" | "completed",
 *   stops: RouteStop[], back: { eta: string, from: string, to: string }, pickups: Pickup[] }} Route
 * @typedef {{ items: Route[], next: { date: string, from: string, to: string } | null }} RouteList
 */

/** The rider's route for a day, in the caller's language (GET /v1/routes?date=, M7). @param {string} lang */
export function useMyRoute(lang, date = todayIst()) {
  return useQuery({
    queryKey: ["routes", date, lang],
    queryFn: () =>
      /** @type {Promise<RouteList>} */ (request(`/v1/routes?${new URLSearchParams({ date })}`)),
    networkMode: "always", // no signal and nothing cached: show "Your route didn't load", not a skeleton
  });
}

/**
 * Rider actions: each is saved in the outbox (sent now, or when there is signal again) and
 * applied to the cached route straight away.
 */
export function useRiderActions() {
  const client = useQueryClient();
  /** @param {Route} route @param {(r: Route) => Route} change @param {{ path: string, body?: unknown }} action */
  const act = (route, change, action) => {
    // Any day's list: the server may answer "today" with the route of the next delivery day.
    client.setQueriesData({ queryKey: ["routes"] }, (/** @type {any} */ list) =>
      list?.items
        ? {
            ...list,
            items: list.items.map((/** @type {Route} */ r) => (r.id === route.id ? change(r) : r)),
          }
        : list,
    );
    return enqueue({ method: "POST", ...action });
  };
  return {
    /** POST /v1/routes/{id}/start @param {Route} route */
    start: (route) =>
      act(route, (r) => ({ ...r, status: "started" }), { path: `/v1/routes/${route.id}/start` }),
    /** POST /v1/routes/{id}/complete @param {Route} route */
    complete: (route) =>
      act(route, (r) => ({ ...r, status: "completed" }), {
        path: `/v1/routes/${route.id}/complete`,
      }),
    /**
     * POST /v1/stops/{id}/handover with the crates, the bag count and the captain's code or a photo
     * (a small JPEG data URL, so it can wait in the outbox; the server stores it and keeps a key).
     * @param {Route} route @param {RouteStop} stop @param {{ otp?: string, photo_jpeg?: string }} proof
     */
    handover: (route, stop, proof) =>
      act(
        route,
        (r) => ({
          ...r,
          stops: r.stops.map((x) => (x.id === stop.id ? { ...x, status: "done" } : x)),
        }),
        {
          path: `/v1/stops/${stop.id}/handover`,
          body: { crate_ids: stop.crates.map((c) => c.id), bags_count: stop.bags_count, ...proof },
        },
      ),
    /** POST /v1/pickups/{id}/collect, with the scanned or typed code if there was one. @param {Route} route @param {Pickup} pickup @param {string} [code] */
    collect: (route, pickup, code) =>
      act(
        route,
        (r) => ({
          ...r,
          pickups: r.pickups.map((p) => (p.id === pickup.id ? { ...p, status: "collected" } : p)),
        }),
        { path: `/v1/pickups/${pickup.id}/collect`, body: code ? { code } : {} },
      ),
  };
}

/**
 * @typedef {{ date: string, routes: number, societies: number, pickups: boolean, amount_paise: number }} EarningsItem
 * @typedef {{ total_paise: number, routes: number, pickup_runs: number, payout_weekday: number, items: EarningsItem[] }} Earnings
 */

/** The rider's latest routes and what they earned, from the ledger (GET /v1/earnings, M8). */
export function useEarnings() {
  return useQuery({
    queryKey: ["earnings"],
    queryFn: () => /** @type {Promise<Earnings>} */ (request("/v1/earnings")),
  });
}

/** A short-lived link to the income certificate PDF (GET /v1/earnings/certificate, M8 US-7). */
export const certificateUrl = async () =>
  /** @type {{ url: string }} */ (await request("/v1/earnings/certificate")).url;
