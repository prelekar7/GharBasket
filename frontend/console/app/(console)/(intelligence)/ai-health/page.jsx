import { apiGet } from "@/lib/api.js";
import { apiSource, parseSource } from "@/lib/data-source.js";
import { currentHub } from "@/lib/hub.js";
import { AIHealthView } from "./AIHealthView.jsx";

export const metadata = { title: "AI health" };

/**
 * M10 AI health: live model versions, drift, forecast vs actual and how often the rule-based fallback
 * answered. One data source at a time (`?source=`).
 * @param {{ searchParams: Promise<{ source?: string }> }} props
 */
export default async function Page({ searchParams }) {
  const source = parseSource((await searchParams).source);
  const hub = await currentHub();
  const health = await apiGet("/v1/intel/health", {
    searchParams: { hub_id: hub.id, data_source: apiSource(source) },
  });
  return <AIHealthView health={health} source={source} hubName={hub.name} />;
}
