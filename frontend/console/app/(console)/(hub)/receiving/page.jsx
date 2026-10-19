import { ApiProblem, apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { todayIso } from "@/lib/catalog.js";
import { currentHub } from "@/lib/hub.js";
import { opsUnit, STOCKED_KINDS } from "@/lib/ops-units.js";
import { ReceivingView } from "./ReceivingView.jsx";

export const metadata = { title: "Receiving and QC" };

/**
 * Today's receiving (M6 §3.4): the purchase orders that were sent, the receipts weighed in so far, and
 * what an ad hoc market buy needs (active vendors, stocked products). The plan only says how much
 * families need today; a day with no plan just leaves that sentence out.
 */
export default async function Page() {
  const hub = await currentHub();
  const [orders, receipts, vendors, products, plan] = await Promise.all([
    apiGet(`/v1/hubs/${hub.id}/purchase-orders`),
    apiGet(`/v1/hubs/${hub.id}/goods-receipts`),
    apiGet(`/v1/hubs/${hub.id}/vendors`, { searchParams: { active: true } }),
    apiAll("/v1/products"),
    apiGet(`/v1/hubs/${hub.id}/procurement-plans`).catch((e) => {
      if (e instanceof ApiProblem && e.code === "hub_ops.plan_not_found") return null;
      throw e;
    }),
  ]);
  return (
    <ReceivingView
      date={todayIso()}
      orders={orders.items.filter((/** @type {{ status: string }} */ o) =>
        ["sent", "partly_received", "received"].includes(o.status),
      )}
      receipts={receipts.items}
      vendors={vendors.items}
      products={products.items
        .filter((/** @type {{ kind: string }} */ p) => STOCKED_KINDS.includes(p.kind))
        .map((/** @type {{ id: string, name: string, step: { unit: string } }} */ p) => ({
          id: p.id,
          name: p.name,
          unit: opsUnit(p.step),
        }))}
      committed={Object.fromEntries(
        (plan?.lines ?? []).map(
          (/** @type {{ product_id: string, committed_qty: number }} */ l) => [
            l.product_id,
            l.committed_qty,
          ],
        ),
      )}
    />
  );
}
