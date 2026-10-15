import { connection } from "next/server";
import { createLoader, parseAsString } from "nuqs/server";
import { apiGet } from "@/lib/api.js";
import { apiAll } from "@/lib/api-pages.js";
import { deliveryDates } from "@/lib/delivery-dates.js";
import { currentHub } from "@/lib/hub.js";
import { orNotShipped } from "@/lib/proposal.js";
import { OrdersView } from "./OrdersView.jsx";

export const metadata = { title: "Orders" };

const loadParams = createLoader({ date: parseAsString });

/**
 * Orders board of the selected hub for one delivery day. The day is a server-side filter (`from` and
 * `to` of GET /v1/orders, which the page follows through every page of results); slot, society and
 * kind are narrowed in the browser, so they switch at once. Society names come from the public society
 * list: an order has only `society_id`.
 * @param {{ searchParams: Promise<import("nuqs/server").SearchParams> }} props
 */
export default async function Page({ searchParams }) {
  await connection(); // "now" for the cut-off countdown
  const hub = await currentHub();
  const [{ date: picked }, days] = await Promise.all([
    loadParams(searchParams),
    deliveryDates(hub.id),
  ]);
  const date = picked && days.includes(picked) ? picked : days[0];
  const [orders, societies, cutoff] = await Promise.all([
    apiAll("/v1/orders", { hub_id: hub.id, from: date, to: date }),
    apiGet("/v1/societies"),
    // @proposal (not in the contract yet): GET /v1/hubs/{id}/cutoff, the cut-off monitor's read
    orNotShipped(apiGet(`/v1/hubs/${hub.id}/cutoff`)),
  ]);
  return (
    <OrdersView
      date={date}
      dates={days}
      orders={orders.items}
      societyNames={Object.fromEntries(
        societies.items.map((/** @type {{ id: string, name: string }} */ s) => [s.id, s.name]),
      )}
      // every order of a day has the same cut-off, which the order itself carries
      cutoffAt={orders.items[0]?.cutoff_at ?? null}
      asOf={cutoff?.as_of ?? new Date().toISOString()}
    />
  );
}
