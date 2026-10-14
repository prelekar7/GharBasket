import { loadUnitEconomics } from "./data.js";
import { UnitEconomicsView } from "./UnitEconomicsView.jsx";

export const metadata = { title: "Unit economics" };

export default async function Page() {
  return <UnitEconomicsView data={await loadUnitEconomics()} />;
}
