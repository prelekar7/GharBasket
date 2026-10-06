import { apiGet } from "@/lib/api.js";
import { orNotShipped } from "@/lib/proposal.js";
import { PlansView } from "./PlansView.jsx";

export const metadata = { title: "Plans" };

export default async function Page() {
  // @proposal (not in the contract yet): GET /v1/admin/plans adds `state` and `subscriber_count` and
  // comes with the edit endpoints. Without it the page shows the real plans, read-only.
  const admin = await orNotShipped(apiGet("/v1/admin/plans"));
  const { items } = admin ?? (await apiGet("/v1/plans"));
  return <PlansView plans={items} editable={admin !== null} />;
}
