import { apiGet } from "@/lib/api.js";
import { parseSource } from "@/lib/data-source.js";
import { currentHub, listHubs } from "@/lib/hub.js";
import { AIHealthView } from "./AIHealthView.jsx";

export const metadata = { title: "AI health" };

/**
 * M12 AI health (admin): live model versions, drift per hub, and how often the rule-based fallback
 * answered. The API sends every data source; the page shows one at a time (`?source=`).
 * @param {{ searchParams: Promise<{ source?: string }> }} props
 */
export default async function Page({ searchParams }) {
  const source = parseSource((await searchParams).source);
  const [hub, hubs, health] = await Promise.all([
    currentHub(),
    listHubs(),
    apiGet("/v1/analytics/ai-health"),
  ]);
  return (
    <AIHealthView
      health={health}
      source={source}
      hub={hub}
      hubNames={Object.fromEntries(hubs.map((h) => [h.id, h.name]))}
    />
  );
}
