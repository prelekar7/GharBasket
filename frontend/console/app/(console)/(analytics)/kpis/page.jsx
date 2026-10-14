import { loadKpis } from "./data.js";
import { KpisView } from "./KpisView.jsx";

export const metadata = { title: "KPIs" };

/** @param {{ searchParams: Promise<{ period?: string }> }} props */
export default async function Page({ searchParams }) {
  const { period } = await searchParams;
  return <KpisView kpis={await loadKpis(period)} />;
}
