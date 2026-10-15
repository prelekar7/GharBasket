import { useQuery, useQueryClient } from "@tanstack/react-query";
import { todayIst } from "@/features/subscriptions/format.js";
import { request } from "@/lib/api.js";
import { db, enqueue, flush } from "@/lib/outbox.js";

// Rider data (M7 §3.3 and §3.4, frozen). Reads are cached and persisted, so a route loaded at the
// hub keeps working without signal. Writes go through the offline outbox (each tap has its own
// Idempotency-Key and a `client_at`) and patch the cached route at once (optimistic).

/**
 * @typedef {{ id: string, name: string, location: { lat: number, lng: number } }} StopSociety
 * @typedef {{ empties_collected: number | null, donations: Donation[] }} StopPickups
 * @typedef {{ donation_id: string, ref: string, qty: number, note: string, status: "expected" | "collected" | "not_ready" }} Donation
 * @typedef {{ id: string, seq: number, kind: "drop" | "bank" | "kitchen", society: StopSociety | null,
 *   slot: { id: string, label: string, start: string, end: string } | null, eta: string, late_min: number,
 *   eta_now: string | null, status: "pending" | "arrived" | "handed_over" | "skipped", crates: number,
 *   bags: number, redelivery_bags: number, empties_expected: number,
 *   captain: { display_name: string, photo_url: string | null } | null, rider_mode: boolean,
 *   handover_code: string | null, navigate_url: string, arrived_at: string | null,
 *   handed_over_at: string | null, pickups?: StopPickups }} Stop
 * @typedef {{ id: string, plan_id: string, hub_id: string, delivery_date: string, kind: "box" | "tiffin",
 *   trip: number, status: "draft" | "published" | "in_progress" | "completed" | "cancelled",
 *   vehicle: { id: string, label: string, kind: string }, rider: { user_id: string, name: string } | null,
 *   depart: string, return_by: string, stops_total: number, stops_done: number, crates: number,
 *   bags: number, km: number, started_at: string | null, completed_at: string | null, version: number }} RouteSummary
 * @typedef {RouteSummary & { stops: Stop[], source: string, left_behind: string[] }} Route
 * @typedef {{ route: Route | null, next: RouteSummary | null }} MyRoute
 */

const mine = async (/** @type {string} */ date) =>
  /** @type {{ items: RouteSummary[] }} */ (
    await request(`/v1/riders/me/routes?${new URLSearchParams({ date })}`)
  ).items;

/** The route to show: the one in progress, else the first to leave, else the last one done. @param {RouteSummary[]} items */
const pickRoute = (items) => {
  const live = items.filter((r) => r.status !== "cancelled" && r.status !== "draft");
  return (
    live.find((r) => r.status === "in_progress") ??
    live.find((r) => r.status === "published") ??
    live.at(-1) ??
    null
  );
};

/**
 * The rider's route for today with its stops (GET /v1/riders/me/routes, then GET /v1/routes/{id}),
 * or none and tomorrow's route, which riders see the night before.
 * @param {string} lang
 */
export function useMyRoute(lang, date = todayIst()) {
  return useQuery({
    queryKey: ["routes", date, lang],
    queryFn: async () => {
      const pick = pickRoute(await mine(date));
      if (pick) return /** @type {MyRoute} */ ({ route: await routeDetail(pick.id), next: null });
      const tomorrow = new Date(Date.parse(`${date}T12:00:00Z`) + 864e5).toISOString().slice(0, 10);
      return /** @type {MyRoute} */ ({ route: null, next: pickRoute(await mine(tomorrow)) });
    },
    networkMode: "always", // no signal and nothing cached: show "Your route didn't load", not a skeleton
    // While the van waits at a captain's gate, her handover shows up here a few seconds later.
    refetchInterval: (q) =>
      q.state.data?.route?.stops.some((x) => x.status === "arrived" && !x.rider_mode)
        ? 5000
        : false,
  });
}

const routeDetail = (/** @type {string} */ id) =>
  /** @type {Promise<Route>} */ (request(`/v1/routes/${id}`));

/** The hub's name and place, for the map and the way back (GET /v1/hubs/{id}). @param {string | undefined} id */
export function useHub(id) {
  return useQuery({
    queryKey: ["hub", id],
    queryFn: () =>
      /** @type {Promise<{ id: string, name: string, location: { lat: number, lng: number } }>} */ (
        request(`/v1/hubs/${id}`)
      ),
    enabled: !!id,
    staleTime: 60 * 60_000,
  });
}

/**
 * Saves a write in the outbox with its `client_at`, and once the outbox is empty reads the routes
 * again, so what only the server knows (codes, a captain's handover) shows up.
 * @param {import("@tanstack/react-query").QueryClient} client @param {{ path: string, body?: Record<string, unknown> }} action
 */
async function send(client, action) {
  const key = await enqueue({
    method: "POST",
    path: action.path,
    body: { ...action.body, client_at: new Date().toISOString() },
  });
  flush()
    .then(async () => {
      const waiting = await db.table("outbox").where("status").equals("waiting").count();
      if (navigator.onLine && waiting === 0) client.invalidateQueries({ queryKey: ["routes"] });
    })
    .catch(() => {});
  return key;
}

/**
 * Rider actions: each is saved in the outbox (sent now, or when there is signal again) and
 * applied to the cached route straight away. Once the outbox is empty the route is read again, so
 * what only the server knows (handover codes after start, a captain's handover) shows up.
 */
export function useRiderActions() {
  const client = useQueryClient();
  /** @param {Route} route @param {(r: Route) => Route} change @param {{ path: string, body?: Record<string, unknown> }} action */
  const act = async (route, change, action) => {
    // Any day's list: the server may answer "today" with the route of the next delivery day.
    client.setQueriesData({ queryKey: ["routes"] }, (/** @type {any} */ data) =>
      data?.route?.id === route.id ? { ...data, route: change(data.route) } : data,
    );
    return send(client, action);
  };
  /** @param {string} id @param {(s: Stop) => Stop} change @returns {(r: Route) => Route} */
  const onStop = (id, change) => (r) => ({
    ...r,
    stops: r.stops.map((x) => (x.id === id ? change(x) : x)),
  });
  const now = () => new Date().toISOString();
  return {
    /** POST /v1/routes/{id}/start @param {Route} route */
    start: (route) =>
      act(route, (r) => ({ ...r, status: "in_progress", started_at: now() }), {
        path: `/v1/routes/${route.id}/start`,
      }),
    /** POST /v1/routes/{id}/complete @param {Route} route @param {number} [empties] */
    complete: (route, empties) =>
      act(route, (r) => ({ ...r, status: "completed", completed_at: now() }), {
        path: `/v1/routes/${route.id}/complete`,
        body: empties === undefined ? {} : { empties_returned: empties },
      }),
    /** POST /v1/stops/{id}/arrive: the van is at the gate; the captain gets a push. @param {Route} route @param {Stop} stop */
    arrive: (route, stop) =>
      act(
        route,
        onStop(stop.id, (x) => ({ ...x, status: "arrived", arrived_at: now() })),
        {
          path: `/v1/stops/${stop.id}/arrive`,
        },
      ),
    /** POST /v1/stops/{id}/skip @param {Route} route @param {Stop} stop @param {"captain_absent" | "no_access" | "vehicle_issue" | "other"} reason */
    skip: (route, stop, reason) =>
      act(
        route,
        onStop(stop.id, (x) => ({ ...x, status: "skipped" })),
        {
          path: `/v1/stops/${stop.id}/skip`,
          body: { reason },
        },
      ),
    /**
     * POST /v1/stops/{id}/handover at a rider-mode stop: the crates and bags the rider counted.
     * A captain's stop is handed over from her phone with the code the rider shows.
     * @param {Route} route @param {Stop} stop @param {{ crates: number, bags: number }} counted
     */
    handover: (route, stop, counted) =>
      act(
        route,
        onStop(stop.id, (x) => ({ ...x, status: "handed_over", handed_over_at: now() })),
        {
          path: `/v1/stops/${stop.id}/handover`,
          body: counted,
        },
      ),
    /**
     * POST /v1/stops/{id}/pickups on the way back (M7 §3.6, an outline): empties counted and
     * donations collected or not ready.
     * @param {Route} route @param {Stop} stop
     * @param {{ empties?: number, donations?: { donation_id: string, status: "collected" | "not_ready" }[] }} picked
     */
    pickups: (route, stop, picked) =>
      act(
        route,
        onStop(stop.id, (x) => ({
          ...x,
          pickups: {
            empties_collected: picked.empties ?? x.pickups?.empties_collected ?? null,
            donations: (x.pickups?.donations ?? []).map((d) => {
              const got = picked.donations?.find((p) => p.donation_id === d.donation_id);
              return got ? { ...d, status: got.status } : d;
            }),
          },
        })),
        { path: `/v1/stops/${stop.id}/pickups`, body: picked },
      ),
  };
}

/**
 * @typedef {{ order_id: string, flat_label: string, kind: "box" | "addon" | "tiffin", bags: number,
 *   redelivery: boolean, status: "on_the_way" | "with_you" | "held" | "delivered" | "undelivered",
 *   hold_until: string | null }} Door
 */

/** The bags to deliver at a rider-mode stop (GET /v1/stops/{id}/deliveries). @param {string} stopId @param {boolean} enabled */
export function useDoors(stopId, enabled) {
  return useQuery({
    queryKey: ["routes", "doors", stopId],
    queryFn: async () =>
      /** @type {Door[]} */ ((await request(`/v1/stops/${stopId}/deliveries`)).items),
    enabled,
    networkMode: "always",
  });
}

/**
 * Door proof and undelivered at a rider-mode stop, through the outbox; the flat changes at once.
 * @param {string} stopId
 */
export function useDoorActions(stopId) {
  const client = useQueryClient();
  /** @param {Door} door @param {Door["status"]} status @param {string} path @param {Record<string, unknown>} body */
  const door = (door, status, path, body) => {
    client.setQueryData(["routes", "doors", stopId], (/** @type {Door[] | undefined} */ doors) =>
      doors?.map((d) => (d.order_id === door.order_id ? { ...d, status } : d)),
    );
    return send(client, { path, body });
  };
  return {
    /**
     * POST /v1/deliveries/{order_id}/proof: a rider needs the customer's code or a photo.
     * @param {Door} d @param {{ proof: "otp", delivery_code: string } | { proof: "photo", photo_key: string }} proof
     */
    deliver: (d, proof) =>
      door(d, "delivered", `/v1/deliveries/${d.order_id}/proof`, {
        bag_qr: `GB1:${d.order_id.replaceAll("-", "")}`,
        ...proof,
      }),
    /** POST /v1/deliveries/{order_id}/undelivered @param {Door} d @param {"not_home" | "refused" | "wrong_address" | "damaged" | "missing"} reason */
    undeliver: (d, reason) =>
      door(d, "undelivered", `/v1/deliveries/${d.order_id}/undelivered`, { reason }),
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
