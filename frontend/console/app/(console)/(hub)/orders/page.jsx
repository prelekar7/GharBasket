import { createLoader, parseAsString } from "nuqs/server";
import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { OrdersView } from "./OrdersView.jsx";

export const metadata = { title: "Orders" };

const loadParams = createLoader({ date: parseAsString });

/**
 * Orders board of the selected hub for one delivery day. The day is a server-side filter (the
 * `date` query param); slot, society and kind are narrowed in the browser, so they switch at once.
 * @param {{ searchParams: Promise<import("nuqs/server").SearchParams> }} props
 */
export default async function Page({ searchParams }) {
  const hub = await currentHub();
  const [{ date: picked }, dates, cutoff] = await Promise.all([
    loadParams(searchParams),
    apiGet(`/v1/hubs/${hub.id}/delivery-dates`),
    apiGet(`/v1/hubs/${hub.id}/cutoff`),
  ]);
  const days = /** @type {{ date: string }[]} */ (dates.items).map((d) => d.date);
  const date = picked && days.includes(picked) ? picked : (days[0] ?? cutoff.delivery_date);
  const [orders, slots] = await Promise.all([
    apiGet("/v1/orders", { searchParams: { hub_id: hub.id, date } }),
    apiGet("/v1/slots", { searchParams: { hub_id: hub.id, date } }),
  ]);
  return (
    <OrdersView
      date={date}
      dates={days}
      orders={orders.items}
      slots={slots.items}
      cutoffAt={date === cutoff.delivery_date ? cutoff.cutoff_at : null}
      asOf={cutoff.as_of}
    />
  );
}
