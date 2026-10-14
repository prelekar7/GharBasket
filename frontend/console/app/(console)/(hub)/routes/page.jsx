import { getRoutes } from "./data.js";
import { RoutesView } from "./RoutesView.jsx";

export const metadata = { title: "Routes" };

/** @param {{ searchParams: Promise<{ slot?: string }> }} props */
export default async function Page({ searchParams }) {
  const { slot } = await searchParams;
  const slotId = slot === "pm" ? "pm" : "am";
  const { date, items } = await getRoutes(slotId);
  return <RoutesView slot={slotId} date={date} routes={items} />;
}
