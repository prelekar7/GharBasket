import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { DeliveryDaysView } from "./DeliveryDaysView.jsx";

export const metadata = { title: "Delivery days" };

/**
 * @typedef {{ society_id: string, name: string, area: string, slot_id: string, weekdays: number[] }} SocietyDays
 * @typedef {{ items: SocietyDays[], slots: { id: string, label: string }[], not_live: string[], needs_hub: string[] }} DeliveryDays
 */

export default async function Page() {
  const hub = await currentHub();
  /** @type {DeliveryDays} */
  const data = await apiGet(`/v1/hubs/${hub.id}/delivery-days`);
  return <DeliveryDaysView data={data} />;
}
