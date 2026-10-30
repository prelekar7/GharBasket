import { PageHead } from "@/components/admin/Page.jsx";
import { NOT_SHIPPED_MESSAGE } from "@/lib/proposal.js";
import { loadPnl } from "./data.js";
import { PnlView } from "./PnlView.jsx";

export const metadata = { title: "P&L" };

/** @param {{ searchParams: Promise<{ period?: string, source?: string }> }} props */
export default async function Page({ searchParams }) {
  const { period, source } = await searchParams;
  const pnl = await loadPnl(period, source);
  if (!pnl) return <PageHead title="P&L">{NOT_SHIPPED_MESSAGE}</PageHead>;
  return <PnlView pnl={pnl} />;
}
