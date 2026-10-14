import { createLoader, parseAsString } from "nuqs/server";
import { NoAccess } from "@/components/admin/Page.jsx";
import { isForbidden } from "@/lib/admin/safe.js";
import { ApiProblem, apiGet } from "@/lib/api.js";
import { isNotShipped } from "@/lib/proposal.js";
import { DeliveryLogView } from "./DeliveryLogView.jsx";

export const metadata = { title: "Delivery log" };

const loadParams = createLoader({
  phone: parseAsString,
  event: parseAsString,
  cursor: parseAsString,
});

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * @typedef {{ channel: string, status: string, attempts: number, last_error: string | null, provider_ref: string | null,
 *   next_attempt_at: string | null, sent_at: string | null }} Delivery
 * @typedef {{ id: string, event_id: string, kind: string, event_type: string, category: string, title: string, body: string,
 *   link: string | null, created_at: string, read_at: string | null, deliveries: Delivery[] }} LogEntry
 * @typedef {{ id: string, name: string, phone_e164: string, locale: string }} Person
 */

/**
 * What one person was sent (a support tool: "I never got the reminder"). The log is by user id
 * (M11 §3.2). A mobile number is turned into the id with the proposed `GET /v1/admin/users?phone=`
 * (not in the contract yet); until it ships, the field also takes the person's user id.
 * @param {{ searchParams: Promise<import("nuqs/server").SearchParams> }} props
 */
export default async function Page({ searchParams }) {
  const { phone, event, cursor } = await loadParams(searchParams);
  /** @type {{ userId: string | null, person: Person | null, items: LogEntry[], next_cursor: string | null } | null} */
  let result = null;
  let error = "";
  if (phone) {
    try {
      const typed = phone.trim();
      /** @type {Person | null} */
      let person = null;
      if (!UUID.test(typed)) {
        try {
          const { items } = await apiGet("/v1/admin/users", { searchParams: { phone: typed } });
          person = items[0] ?? null;
        } catch (e) {
          if (isNotShipped(e))
            error =
              "Searching by number needs a backend endpoint that has not shipped. Paste the person’s user id instead.";
          else if (e instanceof ApiProblem && e.status === 422)
            error = "Enter the 10-digit mobile number.";
          else throw e;
        }
      }
      const userId = person?.id ?? (UUID.test(typed) ? typed.toLowerCase() : null);
      if (userId && !error) {
        // Text is rendered in the request language: the person's own, as it was sent to them.
        const language = person?.locale.slice(0, 2);
        const page = await apiGet("/v1/admin/notifications", {
          searchParams: { user_id: userId, cursor },
          headers: language ? { "accept-language": language } : undefined,
        });
        result = { userId, person, items: page.items, next_cursor: page.next_cursor };
      } else if (!error) {
        result = { userId: null, person: null, items: [], next_cursor: null };
      }
    } catch (e) {
      if (isForbidden(e)) return <NoAccess title="Delivery log" />;
      if (!(e instanceof ApiProblem)) throw e;
      error = "We couldn’t search just now. Try again.";
    }
  }
  return (
    <DeliveryLogView
      phone={phone ?? ""}
      event={event ?? "all"}
      cursor={cursor}
      result={result}
      error={error}
    />
  );
}
