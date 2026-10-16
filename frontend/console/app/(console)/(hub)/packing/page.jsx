import { createLoader, parseAsString } from "nuqs/server";
import { apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { currentHub } from "@/lib/hub.js";
import { hubDays, pickDay } from "@/lib/hub-days.js";
import { PackingView } from "./PackingView.jsx";

export const metadata = { title: "Packing" };

const loadParams = createLoader({ date: parseAsString });

/**
 * The packing floor of one day (M6 §3.2): the counts and each society's load from the summary, and the
 * queue itself (every page of it, by seq).
 * @param {{ searchParams: Promise<import("nuqs/server").SearchParams> }} props
 */
export default async function Page({ searchParams }) {
  const hub = await currentHub();
  const days = hubDays(3, 2);
  const date = pickDay((await loadParams(searchParams)).date, days);
  const [summary, tasks] = await Promise.all([
    apiGet(`/v1/hubs/${hub.id}/packing-summary`, { searchParams: { date } }),
    apiAll(`/v1/hubs/${hub.id}/packing-tasks`, { date }, { limit: 200 }),
  ]);
  return <PackingView date={date} days={days} summary={summary} tasks={tasks.items} />;
}
