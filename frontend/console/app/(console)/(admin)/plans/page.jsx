import { NoAccess } from "@/components/admin/Page.jsx";
import { isForbidden } from "@/lib/admin/safe.js";
import { apiGet } from "@/lib/api.js";
import { PlansView } from "./PlansView.jsx";

export const metadata = { title: "Plans" };

export default async function Page() {
  try {
    // GET /v1/admin/plans (M3 §3.5, admin): every plan, live or draft, with its subscriber count.
    // Read-only by decision: plans are seeded and a fee is a price, so there is no create or edit.
    const { items } = await apiGet("/v1/admin/plans");
    return <PlansView plans={items} />;
  } catch (error) {
    if (isForbidden(error)) return <NoAccess title="Plans" />;
    throw error;
  }
}
