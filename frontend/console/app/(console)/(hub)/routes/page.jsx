import { getRoutes } from "./data.js";
import { RoutesView } from "./RoutesView.jsx";

export const metadata = { title: "Routes" };

/** @param {{ searchParams: Promise<{ day?: string }> }} props */
export default async function Page({ searchParams }) {
  const { day } = await searchParams;
  const data = await getRoutes(day === "tomorrow" ? "tomorrow" : "today");
  return <RoutesView {...data} />;
}
