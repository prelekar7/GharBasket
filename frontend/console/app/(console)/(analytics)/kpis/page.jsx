import { loadKpis } from "./data.js";
import { KpisView } from "./KpisView.jsx";

export const metadata = { title: "KPIs" };

/** @param {{ searchParams: Promise<{ period?: string, hub?: string }> }} props */
export default async function Page({ searchParams }) {
  const { period, hub } = await searchParams;
  return <KpisView kpis={await loadKpis(period, hub)} />;
}
