import { createLoader, parseAsString } from "nuqs/server";
import { ApiProblem, apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { hubDays, pickDay } from "@/lib/hub-days.js";
import { ProcurementView } from "./ProcurementView.jsx";

export const metadata = { title: "Procurement" };

const loadParams = createLoader({ date: parseAsString });

/**
 * The plan of one delivery day (M6 §3.3), its purchase orders, and the hub's active vendors for the
 * line sheet. A day with no plan yet is a 404 `hub_ops.plan_not_found`, which the view shows as "not built".
 * @param {{ searchParams: Promise<import("nuqs/server").SearchParams> }} props
 */
export default async function Page({ searchParams }) {
  const hub = await currentHub();
  const days = hubDays(0, 2);
  const date = pickDay((await loadParams(searchParams)).date, days);
  const [plan, orders, vendors] = await Promise.all([
    apiGet(`/v1/hubs/${hub.id}/procurement-plans`, { searchParams: { date } }).catch((e) => {
      if (e instanceof ApiProblem && e.code === "hub_ops.plan_not_found") return null;
      throw e;
    }),
    apiGet(`/v1/hubs/${hub.id}/purchase-orders`, { searchParams: { date } }),
    apiGet(`/v1/hubs/${hub.id}/vendors`, { searchParams: { active: true } }),
  ]);
  return (
    <ProcurementView
      date={date}
      days={days}
      plan={plan}
      orders={orders.items}
      vendors={vendors.items}
    />
  );
}
