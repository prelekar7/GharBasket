import { apiGet } from "@/lib/api.js";
import { HandoffsView } from "./HandoffsView.jsx";

export const metadata = { title: "NGO hand-offs" };

/** M9 NGO hand-offs: what the hub gave the NGO, where it went, and the monthly report. */
export default async function Page() {
  const data = await apiGet("/v1/ngo-handoffs", { searchParams: { month: "2026-10" } });
  return <HandoffsView data={data} />;
}
