import { loadUnitEconomics } from "./data.js";
import { UnitEconomicsView } from "./UnitEconomicsView.jsx";

export const metadata = { title: "Unit economics" };

/** @param {{ searchParams: Promise<{ source?: string }> }} props */
export default async function Page({ searchParams }) {
  const { source } = await searchParams;
  return <UnitEconomicsView data={await loadUnitEconomics(source)} />;
}
