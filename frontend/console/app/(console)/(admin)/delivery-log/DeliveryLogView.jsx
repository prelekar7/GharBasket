"use client";

import { Badge, Button, EmptyState, Select, TextField } from "@gharbasket/ui";
import { parseAsString, useQueryStates } from "nuqs";
import { useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { dayTimeLabel } from "@/lib/when.js";

const EVENTS = [
  { value: "all", label: "All events" },
  { value: "cycle", label: "Week ready" },
  { value: "reminder", label: "Cut-off reminder" },
  { value: "delivery", label: "Delivery updates" },
];

const th = "border-b border-line-strong px-3 py-2.5 font-semibold whitespace-nowrap";

/** "9800001001" → "98000 01001". @param {string} digits */
const spaced = (digits) =>
  digits.length > 5 ? `${digits.slice(0, 5)} ${digits.slice(5)}` : digits;

/** @type {Record<LogStatus, { tone: "primary" | "neutral" | "danger", label: string }>} */
const STATUS = {
  delivered: { tone: "primary", label: "Delivered" },
  not_sent: { tone: "neutral", label: "Not sent" },
  failed: { tone: "danger", label: "Failed" },
};
/** @typedef {import("@/mocks/notifications.js").LogRow["status"]} LogStatus */

/**
 * Delivery log: search by mobile number and see what that person was sent, on which channel, and
 * whether it arrived.
 * @param {{ phone: string, event: string, error: string,
 *   result: { person: import("./page.jsx").Person | null, items: import("./page.jsx").LogRow[] } | null }} props
 */
export function DeliveryLogView({ phone, event, result, error }) {
  const [pending, startTransition] = useTransition();
  const [, setQuery] = useQueryStates(
    { phone: parseAsString, event: parseAsString },
    { shallow: false, startTransition },
  );
  const [typed, setTyped] = useState(spaced(phone));
  const [chosen, setChosen] = useState(event);

  /** @param {import("react").FormEvent} e */
  function search(e) {
    e.preventDefault();
    const digits = typed.replace(/\D/g, "").replace(/^91(?=\d{10}$)/, "");
    setQuery({ phone: digits || null, event: chosen === "all" ? null : chosen });
  }

  return (
    <>
      <PageHead title="Delivery log">
        What a person was sent, on which channel, and whether it arrived. For support calls: “I
        never got the reminder.”
      </PageHead>

      <form onSubmit={search} className="flex flex-wrap items-end gap-3" noValidate>
        <TextField
          label="Mobile number"
          type="tel"
          inputMode="tel"
          prefix="+91"
          value={typed}
          onChange={setTyped}
          isInvalid={error !== ""}
          errorMessage={error}
          className="max-w-[300px] min-w-[240px] flex-1"
        />
        <div className="w-[240px]">
          <Select label="Event" options={EVENTS} value={chosen} onChange={setChosen} />
        </div>
        <Button type="submit" isDisabled={pending}>
          Search
        </Button>
      </form>

      {result === null ? (
        !error && (
          <EmptyState
            title="Search by mobile number"
            body="Type the number the person signed up with to see their messages."
          />
        )
      ) : result.person === null ? (
        <EmptyState
          title="No one with that number"
          body="Check the digits. Only people who have signed up appear here."
        />
      ) : (
        <>
          <p className="text-sm leading-5 text-fg-secondary">
            {result.person.name} · {result.person.society} · push{" "}
            {result.person.push ? "on" : "off"}, WhatsApp{" "}
            {result.person.whatsapp ? "on" : `off (${result.person.whatsapp_note})`}
          </p>
          {result.items.length === 0 ? (
            <EmptyState title="Nothing sent" body="No messages match this event for this person." />
          ) : (
            <div
              aria-busy={pending}
              className={`relative overflow-x-auto rounded-md border border-line ${pending ? "opacity-60" : ""}`}
            >
              <table className="w-full min-w-[760px] border-collapse text-sm leading-5">
                <caption className="sr-only">Messages sent to {result.person.name}</caption>
                <thead>
                  <tr className="text-left text-fg-secondary">
                    <th scope="col" className={th}>
                      When
                    </th>
                    <th scope="col" className={th}>
                      Message
                    </th>
                    <th scope="col" className={th}>
                      Channel
                    </th>
                    <th scope="col" className={th}>
                      Template
                    </th>
                    <th scope="col" className={th}>
                      Status
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {result.items.map((r) => (
                    <tr key={r.id} className="border-b border-line align-top last:border-b-0">
                      <td className="px-3 py-2.5 whitespace-nowrap">{dayTimeLabel(r.at)}</td>
                      <td className="px-3 py-2.5">{r.message}</td>
                      <td className="px-3 py-2.5">{r.channel}</td>
                      <td className="px-3 py-2.5">
                        <code>{r.template}</code>
                      </td>
                      <td className="px-3 py-2.5">
                        <Badge tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Badge>
                        {r.note && (
                          <div className="text-[13px] leading-[18px] text-fg-secondary">
                            {r.note}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      <p className="text-sm leading-5 text-fg-secondary">
        Message text is shown in the person’s language, as it was sent.
      </p>
    </>
  );
}
