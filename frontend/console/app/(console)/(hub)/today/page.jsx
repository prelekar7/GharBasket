import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { OpsBoardView } from "./OpsBoardView.jsx";

export const metadata = { title: "Ops board" };

export default async function Page() {
  const hub = await currentHub();
  /** @type {import("./OpsBoardView.jsx").OpsBoard} */
  const board = await apiGet(`/v1/hubs/${hub.id}/ops-board`);
  return <OpsBoardView board={board} />;
}
