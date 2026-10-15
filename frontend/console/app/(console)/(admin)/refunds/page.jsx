import { apiGet } from "@/lib/api.js";
import { RefundsView } from "./RefundsView.jsx";

export const metadata = { title: "Refunds" };

/** @typedef {import("@/mocks/billing.js").Refund} Refund */

export default async function Page() {
  /** @type {{ items: Refund[] }} */
  const { items } = await apiGet("/v1/admin/refunds", { searchParams: { status: "pending" } });
  return <RefundsView refunds={items} />;
}
