import { loadPnl } from "./data.js";
import { PnlView } from "./PnlView.jsx";

export const metadata = { title: "P&L" };

/** @param {{ searchParams: Promise<{ period?: string }> }} props */
export default async function Page({ searchParams }) {
  const { period } = await searchParams;
  return <PnlView pnl={await loadPnl(period)} />;
}
