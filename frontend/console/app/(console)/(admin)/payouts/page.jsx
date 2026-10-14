import { getRole } from "@/lib/auth.js";
import { currentHub } from "@/lib/hub.js";
import { getPayouts } from "./data.js";
import { PayoutsView } from "./PayoutsView.jsx";

export const metadata = { title: "Payouts" };

/** @param {{ searchParams: Promise<{ week?: string }> }} props */
export default async function Page({ searchParams }) {
  const { week } = await searchParams;
  const [hub, role] = await Promise.all([currentHub(), getRole()]);
  const data = await getPayouts(hub.id, week);
  return <PayoutsView {...data} canRetry={role === "admin"} />;
}
