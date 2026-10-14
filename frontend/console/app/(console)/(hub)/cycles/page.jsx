import { createLoader, parseAsString } from "nuqs/server";
import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { CyclesView } from "./CyclesView.jsx";

export const metadata = { title: "Cycles" };

const loadParams = createLoader({ week: parseAsString });

/**
 * @typedef {import("@/mocks/cycles.js").CycleRow} CycleRow
 * @typedef {{ week: string, label: string, weeks: { id: string, label: string }[], drafted_at: string,
 *   reminder_at: string, cutoff_at: string, items: CycleRow[], not_live: string[] }} Cycles
 */

/**
 * Weekly cycles of the selected hub, one row per live society.
 * @param {{ searchParams: Promise<import("nuqs/server").SearchParams> }} props
 */
export default async function Page({ searchParams }) {
  const hub = await currentHub();
  const { week } = await loadParams(searchParams);
  /** @type {Cycles} */
  const cycles = await apiGet(`/v1/hubs/${hub.id}/cycles`, { searchParams: { week } });
  return <CyclesView cycles={cycles} />;
}
