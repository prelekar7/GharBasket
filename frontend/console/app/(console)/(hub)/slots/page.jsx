import { createLoader, parseAsString } from "nuqs/server";
import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { SlotsView } from "./SlotsView.jsx";

export const metadata = { title: "Slots" };

const loadParams = createLoader({ date: parseAsString });

/**
 * Delivery slots of the selected hub with that day's bookings.
 * @param {{ searchParams: Promise<import("nuqs/server").SearchParams> }} props
 */
export default async function Page({ searchParams }) {
  const hub = await currentHub();
  const [{ date: picked }, dates] = await Promise.all([
    loadParams(searchParams),
    apiGet(`/v1/hubs/${hub.id}/delivery-dates`),
  ]);
  const days = /** @type {{ date: string }[]} */ (dates.items).map((d) => d.date);
  const date = picked && days.includes(picked) ? picked : days[0];
  const slots = await apiGet("/v1/slots", { searchParams: { hub_id: hub.id, date } });
  return <SlotsView hubId={hub.id} date={date} dates={days} slots={slots.items} />;
}
