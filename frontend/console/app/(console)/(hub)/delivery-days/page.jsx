import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { DeliveryDaysView } from "./DeliveryDaysView.jsx";

export const metadata = { title: "Delivery days" };

/**
 * One delivery day as the contract has it: `weekday` 0 is Monday, `slot_id` the slot that day.
 * @typedef {{ weekday: number, slot_id: string }} Day
 * @typedef {"live" | "unlocking" | "prospect" | "paused"} SocietyStatus
 * @typedef {{ society_id: string, name: string, area: string, status: SocietyStatus, days: Day[] }} SocietyDays
 * @typedef {{ items: SocietyDays[], slots: { id: string, label: string }[] }} DeliveryDays
 * @typedef {{ weekday: number, slot: { id: string, label: string } }} DayOut contract DeliveryDayOut
 */

export default async function Page() {
  const hub = await currentHub();
  const [days, slots] = await Promise.all([
    // M3 §3.5: every society of the hub, whatever its status (live, unlocking, prospect, paused), with its days.
    apiGet(`/v1/hubs/${encodeURIComponent(hub.id)}/delivery-days`),
    apiGet("/v1/slots", { searchParams: { hub_id: hub.id } }),
  ]);
  /** @type {DeliveryDays} */
  const data = {
    items: days.items.map((/** @type {SocietyDays & { items: DayOut[] }} */ s) => ({
      society_id: s.society_id,
      name: s.name,
      area: s.area,
      status: s.status,
      days: s.items.map((d) => ({ weekday: d.weekday, slot_id: d.slot.id })),
    })),
    // only box slots can carry a society's boxes
    slots: slots.items
      .filter((/** @type {{ kind: string }} */ s) => s.kind === "box")
      .map((/** @type {{ id: string, label: string }} */ s) => ({ id: s.id, label: s.label })),
  };
  return <DeliveryDaysView data={data} />;
}
