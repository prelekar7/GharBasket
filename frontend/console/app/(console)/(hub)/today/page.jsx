import { PageHead } from "@/components/admin/Page.jsx";
import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { NOT_SHIPPED_MESSAGE, orNotShipped } from "@/lib/proposal.js";
import { OpsBoardView } from "./OpsBoardView.jsx";

export const metadata = { title: "Ops board" };

export default async function Page() {
  const hub = await currentHub();
  /** @type {import("./board.js").Today | null} */
  const board = await orNotShipped(apiGet(`/v1/analytics/hubs/${hub.id}/today`));
  if (!board) return <PageHead title="Ops board">{NOT_SHIPPED_MESSAGE}</PageHead>;
  return <OpsBoardView board={board} />;
}
