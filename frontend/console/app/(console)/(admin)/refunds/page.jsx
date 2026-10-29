import { apiAll } from "@/lib/api-pages.js";
import { currentHub } from "@/lib/hub.js";
import { RefundsView } from "./RefundsView.jsx";

export const metadata = { title: "Refunds" };

/** @typedef {import("@/mocks/billing.js").RefundRow} RefundRow */

/**
 * What went back for orders in the selected hub over the API's default window (the last 7 delivery days),
 * following `next_cursor` through every page (M5 §3.8). Hub staff must send `hub_id`; admins may omit it
 * for every hub, but the console works in one hub at a time.
 */
export default async function Page() {
  const hub = await currentHub();
  const { items, truncated } = await apiAll("/v1/admin/refunds", { hub_id: hub.id });
  return <RefundsView refunds={/** @type {RefundRow[]} */ (items)} truncated={truncated} />;
}
