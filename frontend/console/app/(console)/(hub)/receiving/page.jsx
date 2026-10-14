import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { ReceivingView } from "./ReceivingView.jsx";

export const metadata = { title: "Receiving and QC" };

export default async function Page() {
  const hub = await currentHub();
  /** @type {import("./ReceivingView.jsx").ReceivingBoard} */
  const board = await apiGet(`/v1/hubs/${hub.id}/receiving`);
  return <ReceivingView board={board} />;
}
