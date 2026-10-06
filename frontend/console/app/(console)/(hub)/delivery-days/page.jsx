import { apiGet } from "@/lib/api.js";
import { currentHub } from "@/lib/hub.js";
import { orNotShipped } from "@/lib/proposal.js";
import { DeliveryDaysView } from "./DeliveryDaysView.jsx";

export const metadata = { title: "Delivery days" };

/**
 * One delivery day as the contract has it: `weekday` 0 is Monday, `slot_id` the slot that day.
 * @typedef {{ weekday: number, slot_id: string }} Day
 * @typedef {{ society_id: string, name: string, area: string, days: Day[] }} SocietyDays
 * @typedef {{ items: SocietyDays[], slots: { id: string, label: string }[], not_live: string[] }} DeliveryDays
 * The hub-level read, GET /v1/hubs/{id}/delivery-days, is a @proposal (not in the contract yet).
 * @typedef {{ weekday: number, slot: { id: string, label: string } }} DayOut contract DeliveryDayOut
 */

/**
 * Every live society's days: the proposed hub-level read when it exists; otherwise the real calls, the
 * public society list for the live ones and `GET /v1/societies/{id}/delivery-days` for each.
 * @param {string} hubId
 * @returns {Promise<{ items: SocietyDays[], not_live: string[] }>}
 */
async function societyDays(hubId) {
  const hub = await orNotShipped(apiGet(`/v1/hubs/${hubId}/delivery-days`));
  if (hub)
    return {
      items: hub.items.map((/** @type {SocietyDays & { items: DayOut[] }} */ s) => ({
        society_id: s.society_id,
        name: s.name,
        area: s.area,
        days: s.items.map((d) => ({ weekday: d.weekday, slot_id: d.slot.id })),
      })),
      not_live: hub.not_live,
    };
  const { items: societies } = await apiGet("/v1/societies");
  const live = societies.filter((/** @type {{ status: string }} */ s) => s.status === "live");
  const items = await Promise.all(
    live.map(async (/** @type {{ id: string, name: string, area: string }} */ s) => {
      const out = await apiGet(`/v1/societies/${s.id}/delivery-days`);
      return {
        society_id: s.id,
        name: s.name,
        area: s.area,
        days: out.items.map((/** @type {DayOut} */ d) => ({
          weekday: d.weekday,
          slot_id: d.slot.id,
        })),
      };
    }),
  );
  return {
    items,
    not_live: societies
      .filter((/** @type {{ status: string }} */ s) => s.status !== "live")
      .map((/** @type {{ name: string }} */ s) => s.name),
  };
}

export default async function Page() {
  const hub = await currentHub();
  const [days, slots] = await Promise.all([
    societyDays(hub.id),
    apiGet("/v1/slots", { searchParams: { hub_id: hub.id } }),
  ]);
  /** @type {DeliveryDays} */
  const data = {
    ...days,
    // only box slots can carry a society's boxes
    slots: slots.items
      .filter((/** @type {{ kind: string }} */ s) => s.kind === "box")
      .map((/** @type {{ id: string, label: string }} */ s) => ({ id: s.id, label: s.label })),
  };
  return <DeliveryDaysView data={data} />;
}
