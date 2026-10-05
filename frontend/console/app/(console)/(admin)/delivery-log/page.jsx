import { createLoader, parseAsString } from "nuqs/server";
import { apiGet } from "@/lib/api.js";
import { fieldErrors } from "@/lib/ops-errors.js";
import { DeliveryLogView } from "./DeliveryLogView.jsx";

export const metadata = { title: "Delivery log" };

const loadParams = createLoader({ phone: parseAsString, event: parseAsString });

/**
 * @typedef {import("@/mocks/notifications.js").LogRow} LogRow
 * @typedef {{ name: string, society: string, push: boolean, whatsapp: boolean, whatsapp_note: string }} Person
 */

/**
 * What one person was sent, looked up by mobile number (a support tool: "I never got the reminder").
 * @param {{ searchParams: Promise<import("nuqs/server").SearchParams> }} props
 */
export default async function Page({ searchParams }) {
  const { phone, event } = await loadParams(searchParams);
  /** @type {{ person: Person | null, items: LogRow[] } | null} */
  let result = null;
  let error = "";
  if (phone) {
    try {
      result = await apiGet("/v1/admin/notification-log", {
        searchParams: { phone, event: event ?? "all" },
      });
    } catch (e) {
      error = fieldErrors(e).phone ?? "We couldn’t search just now. Try again.";
    }
  }
  return (
    <DeliveryLogView phone={phone ?? ""} event={event ?? "all"} result={result} error={error} />
  );
}
