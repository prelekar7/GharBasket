import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { OpsBoardView } from "./OpsBoardView.jsx";

export const metadata = { title: "Ops board" };

export default async function Page() {
  const hub = await currentHub();
  /** @type {import("./board.js").Today} */
  const board = await apiGet(`/v1/analytics/hubs/${hub.id}/today`);
  return <OpsBoardView board={board} />;
}
