import { apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { todayIso } from "@/lib/catalog.js";
import { currentHub } from "@/lib/hub.js";
import { opsUnit, STOCKED_KINDS } from "@/lib/ops-units.js";
import { WasteView } from "./WasteView.jsx";

export const metadata = { title: "Waste and cascade" };

/** @param {string} iso @param {number} n */
function addDays(iso, n) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/**
 * Waste and the surplus cascade (M6 §3.5): today's waste logs, today's surplus offers, the KPI over the
 * last seven days against the seven before, and the lists the forms need (stocked products, kitchens).
 */
export default async function Page() {
  const hub = await currentHub();
  const today = todayIso();
  const [logs, offers, week, before, products, partners] = await Promise.all([
    apiGet(`/v1/hubs/${hub.id}/waste-logs`, {
      searchParams: { from: today, to: today, limit: 200 },
    }),
    apiGet(`/v1/hubs/${hub.id}/surplus-offers`, { searchParams: { date: today } }),
    apiGet(`/v1/hubs/${hub.id}/waste-summary`, {
      searchParams: { from: addDays(today, -6), to: today },
    }),
    apiGet(`/v1/hubs/${hub.id}/waste-summary`, {
      searchParams: { from: addDays(today, -13), to: addDays(today, -7) },
    }),
    apiAll("/v1/products"),
    apiGet(`/v1/hubs/${hub.id}/partners`, { searchParams: { kind: "kitchen", status: "active" } }),
  ]);
  return (
    <WasteView
      date={today}
      logs={logs.items}
      offers={offers.items}
      summary={week}
      previous={before.totals}
      products={products.items
        .filter((/** @type {{ kind: string }} */ p) => STOCKED_KINDS.includes(p.kind))
        .map((/** @type {{ id: string, name: string, step: { unit: string } }} */ p) => ({
          id: p.id,
          name: p.name,
          unit: opsUnit(p.step),
        }))}
      kitchens={partners.items
        .filter(
          (/** @type {{ kind?: string, status?: string }} */ p) =>
            (!p.kind || p.kind === "kitchen") && (!p.status || p.status === "active"),
        )
        .map((/** @type {{ id: string, display_name?: string, name?: string }} */ p) => ({
          id: p.id,
          name: p.display_name ?? p.name ?? "Kitchen",
        }))}
    />
  );
}
