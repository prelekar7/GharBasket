import { createLoader, parseAsString } from "nuqs/server";
import { ApiProblem, apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { todayIso } from "@/lib/catalog.js";
import { deliveryDates } from "@/lib/delivery-dates.js";
import { currentHub } from "@/lib/hub.js";
import { hubDays, packingDays, pickDay } from "@/lib/hub-days.js";
import { PackingView } from "./PackingView.jsx";

export const metadata = { title: "Packing" };

const loadParams = createLoader({ date: parseAsString });

/**
 * The packing floor of one day (M6 §3.2): the counts and each society's load from the summary, and the
 * queue itself (every page of it, by seq). The days on offer are the last three and the hub's coming
 * delivery days, like the Orders page; a day the API will not serve (`hub_ops.date_out_of_range`, it
 * answers from a week ago to two days on) shows today instead.
 * @param {{ searchParams: Promise<import("nuqs/server").SearchParams> }} props
 */
export default async function Page({ searchParams }) {
  const hub = await currentHub();
  const days = packingDays(hubDays(3, 0), await deliveryDates(hub.id));
  const asked = pickDay((await loadParams(searchParams)).date, days);
  /** @param {string} date */
  const read = (date) =>
    Promise.all([
      apiGet(`/v1/hubs/${hub.id}/packing-summary`, { searchParams: { date } }),
      apiAll(`/v1/hubs/${hub.id}/packing-tasks`, { date }, { limit: 200 }),
    ]);
  let date = asked;
  let loaded;
  try {
    loaded = await read(date);
  } catch (error) {
    if (!(error instanceof ApiProblem && error.code === "hub_ops.date_out_of_range")) throw error;
    date = todayIso();
    loaded = await read(date);
  }
  const [summary, tasks] = loaded;
  return <PackingView date={date} days={days} summary={summary} tasks={tasks.items} />;
}
