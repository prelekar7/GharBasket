import { createLoader, parseAsString } from "nuqs/server";
import { apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { currentHub } from "@/lib/hub.js";
import { hubDays, pickDay } from "@/lib/hub-days.js";
import { TiffinBoardView } from "./TiffinBoardView.jsx";

export const metadata = { title: "Tiffin board" };

const loadParams = createLoader({ date: parseAsString });

/**
 * @typedef {{ order_id: string, society: { id: string, name: string }, portions: number, reason: string }} Unassigned
 * @typedef {{ meal: "lunch" | "dinner", orders: number, portions: number, assigned: number, accepted: number,
 *   preparing: number, ready: number, picked_up: number, unassigned: Unassigned[] }} BoardMeal
 * @typedef {{ order_id: string, short_code: string, portions: number, product: string, society: { id: string, name: string },
 *   status: "assigned" | "ready" | "picked_up" | "delivered" | "failed" | "released" }} AssignmentOrder
 * @typedef {{ id: string, meal: "lunch" | "dinner", partner: { id: string, display_name: string },
 *   status: string, round: number, source: "ml" | "fallback" | "manual", portions: number, ready_by: string,
 *   orders: AssignmentOrder[] }} Assignment
 * @typedef {{ meal: "lunch" | "dinner", round: number, source: "ml" | "fallback", model: string, status: string,
 *   gini: number | null, km_per_portion: number | null, at: string }} BoardRun
 * @typedef {{ date: string, meals: BoardMeal[], assignments: Assignment[], runs: BoardRun[] }} TiffinBoard
 */

/**
 * The hub's tiffins for one day (M8 §3.4): per meal, who cooks what, what nobody could take, and the
 * assignment rounds. `GET /v1/hubs/{id}/tiffin-board?date=`.
 * @param {{ searchParams: Promise<import("nuqs/server").SearchParams> }} props
 */
export default async function Page({ searchParams }) {
  const hub = await currentHub();
  const days = hubDays(3, 2);
  const date = pickDay((await loadParams(searchParams)).date, days);
  const [board, partners] = await Promise.all([
    apiGet(`/v1/hubs/${hub.id}/tiffin-board`, { searchParams: { date } }),
    apiAll(`/v1/hubs/${hub.id}/partners`, { kind: "kitchen" }),
  ]);
  const kitchens = partners.items
    .filter((/** @type {{ status: string }} */ p) => p.status === "active" || p.status === "paused")
    .map((/** @type {{ id: string, display_name: string, status: string }} */ p) => ({
      value: p.id,
      label: p.status === "paused" ? `${p.display_name} (on a break)` : p.display_name,
    }));
  return <TiffinBoardView date={date} days={days} board={board} kitchens={kitchens} />;
}
