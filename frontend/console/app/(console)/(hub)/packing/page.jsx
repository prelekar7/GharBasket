import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { PackingView } from "./PackingView.jsx";

export const metadata = { title: "Packing" };

export default async function Page() {
  const hub = await currentHub();
  /** @type {import("./PackingView.jsx").PackingBoard} */
  const board = await apiGet(`/v1/hubs/${hub.id}/packing-tasks`);
  return <PackingView board={board} />;
}
