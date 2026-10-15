// Kitchen partner data (M8 §3.3, §3.4, §3.6; frozen). Reads are cached (and persisted) by TanStack
// Query; writes go through the offline outbox and patch the cache straight away, so the screen
// answers at once even without signal (field roles, Handbook 09 §9.10).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { istDate } from "@/features/subscriptions/format.js";
import { request } from "@/lib/api.js";
import { enqueue } from "@/lib/outbox.js";

/**
 * @typedef {object} PartnerMe The kitchen's Partner (GET /v1/partners/me → items) with her hub's name.
 * @property {string} id
 * @property {"kitchen"} kind
 * @property {string} display_name "Lata Shinde"
 * @property {string} hub_id
 * @property {string} hub_name "Kharghar hub", from GET /v1/hubs/{id}
 * @property {"active" | "paused" | "suspended" | "left"} status
 * @property {{ average: number, count: number } | null} rating Null under 5 ratings.
 * @property {string} verified_at
 */

/**
 * @typedef {"assigned" | "ready" | "picked_up" | "delivered" | "failed" | "released"} OrderStatus
 * @typedef {object} AssignmentOrder One tiffin order of an assignment; the kitchen sees the box's
 * short code and the society, never a flat or a customer.
 * @property {string} order_id
 * @property {string} short_code "T-07"
 * @property {number} portions
 * @property {string} product "Jain thali", in the caller's language
 * @property {{ id: string, name: string }} society
 * @property {OrderStatus} status
 * @typedef {object} Assignment One meal of tiffins for a day, from the fair assignment (M10).
 * @property {string} id
 * @property {string} date YYYY-MM-DD
 * @property {"lunch" | "dinner"} meal
 * @property {"offered" | "accepted" | "preparing" | "ready" | "completed" | "declined" | "expired" | "cancelled"} status
 * @property {number} portions
 * @property {{ product_id: string, name: string, portions: number }[]} dishes
 * @property {AssignmentOrder[]} orders
 * @property {string} respond_by UTC timestamp: answer by then
 * @property {string} ready_by IST "11:30": pack by then
 * @property {string | null} pickup_qr "GB1A:…" once any order is ready; the rider scans it
 * @property {Record<string, boolean> | null} checklist
 */

/** @typedef {{ date: string, items: Assignment[] }} Assignments GET /v1/assignments; no items = no tiffins (`date` is the first item's, else tomorrow) */

/**
 * @typedef {{ date: string, meal: "lunch" | "dinner", max_portions: number, assigned: number, locked: boolean }} CapacityDay
 * @typedef {{ max_portions_limit: number, skills: string[], skill_options: string[], version: number, days: CapacityDay[] }} Capacity
 * GET /v1/kitchen-capacity with the skills and version of GET /v1/kitchens/me
 */

/**
 * @typedef {{ date: string, tiffins: number, amount_paise: number }} EarningDay
 * @typedef {{ total_paise: number, tiffins: number, days: number, items: EarningDay[] }} Earnings This week, from EarningsWeek (GET /v1/earnings)
 */

/** Skills a kitchen can claim (M8 §3.4). */
export const SKILL_OPTIONS = ["veg", "jain", "no_onion_garlic", "diabetic", "egg", "nonveg"];
/** Capacity per portion count the API accepts (0 = no tiffins that meal). */
export const MAX_PORTIONS = 100;
/** The checklist every `ready` carries (M8 §3.4). */
export const CHECKLIST = ["handwash", "hair_cover", "clean_surface", "sealed", "fresh_today"];

/** A read whose words come in the person's language, so the language is part of the key. */
function useKitchenQuery(
  /** @type {string[]} */ name,
  /** @type {() => Promise<unknown>} */ queryFn,
) {
  const { i18n } = useTranslation();
  return useQuery({ queryKey: ["kitchen", ...name, i18n.resolvedLanguage], queryFn });
}

/** Tomorrow's IST date: what GET /v1/assignments answers for when `date` is left out. */
const tomorrow = () => istDate(new Date(Date.now() + 24 * 60 * 60 * 1000));

/** @returns {Promise<Assignment[]>} */
const assignmentsOn = async (/** @type {string} */ query = "") =>
  /** @type {{ items: Assignment[] }} */ (await request(`/v1/assignments${query}`)).items;

export const usePartnerMe = () =>
  /** @type {import("@tanstack/react-query").UseQueryResult<PartnerMe>} */ (
    useKitchenQuery(["me"], async () => {
      const { items } =
        /** @type {{ items: (Omit<PartnerMe, "hub_name"> & { kind: string })[] }} */ (
          await request("/v1/partners/me")
        );
      const me = items.find((p) => p.kind === "kitchen");
      if (!me) throw new Error("No kitchen partner record");
      const hub = /** @type {{ name: string }} */ (await request(`/v1/hubs/${me.hub_id}`));
      return { ...me, hub_name: hub.name };
    })
  );

/** The offers for tomorrow (GET /v1/assignments; no `date` = tomorrow). */
export const useAssignments = () =>
  /** @type {import("@tanstack/react-query").UseQueryResult<Assignments>} */ (
    useKitchenQuery(["assignments"], async () => {
      const items = await assignmentsOn();
      return { date: items[0]?.date ?? tomorrow(), items };
    })
  );

/**
 * What she can cook now: today's assignments (cooking starts at 00:00 on the day) and tomorrow's
 * accepted ones, in date order. Kept under the same key prefix as the offers, so a write patches both.
 */
export const usePrepAssignments = () =>
  /** @type {import("@tanstack/react-query").UseQueryResult<Assignments>} */ (
    useKitchenQuery(["assignments", "prep"], async () => {
      const today = istDate();
      const [now, next] = await Promise.all([assignmentsOn(`?date=${today}`), assignmentsOn()]);
      const items = [...now, ...next.filter((n) => !now.some((a) => a.id === n.id))].sort(
        (a, b) => a.date.localeCompare(b.date) || a.meal.localeCompare(b.meal),
      );
      return { date: items[0]?.date ?? today, items };
    })
  );

export const useCapacity = () =>
  /** @type {import("@tanstack/react-query").UseQueryResult<Capacity>} */ (
    useKitchenQuery(["capacity"], async () => {
      const [cap, kitchen] = await Promise.all([
        /** @type {Promise<{ items: (Omit<CapacityDay, "assigned"> & { assigned_portions: number })[] }>} */ (
          request("/v1/kitchen-capacity")
        ),
        /** @type {Promise<{ skills: string[], version: number }>} */ (request("/v1/kitchens/me")),
      ]);
      return {
        max_portions_limit: MAX_PORTIONS,
        skills: kitchen.skills,
        skill_options: SKILL_OPTIONS,
        version: kitchen.version,
        days: cap.items.map(({ assigned_portions: assigned, ...d }) => ({ ...d, assigned })),
      };
    })
  );

/** This week's earnings: the days that paid, with the portions of each counted from the entries. */
export const useEarnings = () =>
  /** @type {import("@tanstack/react-query").UseQueryResult<Earnings>} */ (
    useKitchenQuery(["earnings"], async () => {
      const w = /** @type {{ total_paise: number, days: { date: string, amount_paise: number }[],
       *   entries: { kind: string, units: number, at: string }[] }} */ (
        await request("/v1/earnings")
      );
      const mine = w.entries.filter((e) => e.kind === "kitchen");
      const items = w.days
        .filter((d) => d.amount_paise > 0)
        .map((d) => ({
          ...d,
          tiffins: mine
            .filter((e) => istDate(new Date(e.at)) === d.date)
            .reduce((n, e) => n + e.units, 0),
        }))
        .sort((a, b) => (a.date < b.date ? 1 : -1));
      return {
        total_paise: w.total_paise,
        tiffins: mine.reduce((n, e) => n + e.units, 0),
        days: items.length,
        items,
      };
    })
  );

/**
 * A field write: patch the cached `name` data now (every language), then queue the request in
 * the outbox, which sends it with its own Idempotency-Key when there is signal. The next fetch
 * brings the server's answer.
 */
export function useKitchenWrite() {
  const client = useQueryClient();
  return (
    /** @type {{ name: string, update: (data: any) => any, method?: string, path: string, body?: unknown }} */ w,
  ) => {
    client.setQueriesData({ queryKey: ["kitchen", w.name] }, (d) => d && w.update(d));
    return enqueue({ method: w.method, path: w.path, body: w.body });
  };
}

/** Orders the kitchen has cooked and packed: ready, or already taken on. @param {AssignmentOrder} o */
export const isPacked = (o) => ["ready", "picked_up", "delivered"].includes(o.status);

/**
 * One dish of an assignment with its orders: ready when every one of its orders is; before the
 * assignment is started it is not started.
 * @param {Assignment} a
 */
export function dishes(a) {
  return a.dishes.map((dish) => {
    const orders = a.orders.filter((o) => o.product === dish.name);
    /** @type {"planned" | "preparing" | "packed"} */
    const status =
      orders.length > 0 && orders.every(isPacked)
        ? "packed"
        : a.status === "accepted"
          ? "planned"
          : "preparing";
    return {
      ...dish,
      orders,
      status,
      societies: [...new Set(orders.map((o) => o.society.name))],
    };
  });
}

/** The assignment with these orders ready (and itself ready when all are). @param {Assignment} a @param {string[]} orderIds */
export function withReady(a, orderIds) {
  const orders = a.orders.map((o) =>
    orderIds.includes(o.order_id) ? { ...o, status: /** @type {OrderStatus} */ ("ready") } : o,
  );
  return {
    ...a,
    orders,
    status: orders.every(isPacked) ? /** @type {const} */ ("ready") : a.status,
  };
}
