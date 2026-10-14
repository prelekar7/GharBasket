"use client";

import { Badge, Button, EmptyState, Select, TextField } from "@gharbasket/ui";
import { parseAsString, useQueryStates } from "nuqs";
import { useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { dayTimeLabel } from "@/lib/when.js";

// The API has no event filter, so these narrow the page that was loaded: by kind or by category.
/** @type {{ value: string, label: string, keep?: (e: LogEntry) => boolean }[]} */
const EVENTS = [
  { value: "all", label: "All events" },
  { value: "cycle", label: "Week ready", keep: (e) => e.kind === "subscriptions.box_ready" },
  {
    value: "reminder",
    label: "Cut-off reminder",
    keep: (e) => e.kind === "subscriptions.cutoff_reminder",
  },
  { value: "delivery", label: "Delivery updates", keep: (e) => e.category === "delivery" },
  { value: "money", label: "Money", keep: (e) => e.category === "money" },
];

const th = "border-b border-line-strong px-3 py-2.5 font-semibold whitespace-nowrap";

/** "9800001001" → "98000 01001". @param {string} digits */
const spaced = (digits) =>
  digits.length > 5 ? `${digits.slice(0, 5)} ${digits.slice(5)}` : digits;

/** The delivery statuses of M11 §3.2. @type {Record<string, { tone: "primary" | "neutral" | "danger", label: string }>} */
const STATUS = {
  queued: { tone: "neutral", label: "Queued" },
  held: { tone: "neutral", label: "Held" },
  sent: { tone: "neutral", label: "Sent" },
  delivered: { tone: "primary", label: "Delivered" },
  read: { tone: "primary", label: "Read" },
  failed: { tone: "danger", label: "Failed" },
  skipped: { tone: "neutral", label: "Not sent" },
};
const CHANNEL = /** @type {Record<string, string>} */ ({ push: "Push", whatsapp: "WhatsApp" });

/**
 * @typedef {import("./page.jsx").LogEntry} LogEntry
 * @typedef {import("./page.jsx").Delivery} Delivery
 */

/**
 * One row per delivery attempt chain; a message with none is only in the inbox.
 * @param {LogEntry} entry
 * @returns {{ key: string, entry: LogEntry, delivery: Delivery | null }[]}
 */
const rowsOf = (entry) =>
  entry.deliveries.length
    ? entry.deliveries.map((delivery) => ({
        key: `${entry.id}:${delivery.channel}`,
        entry,
        delivery,
      }))
    : [{ key: entry.id, entry, delivery: null }];

/** The line under a status: why it failed or was skipped, or when it goes out. @param {Delivery} d */
function note(d) {
  if (d.last_error) return d.last_error;
  if ((d.status === "queued" || d.status === "held") && d.next_attempt_at)
    return `Goes out ${dayTimeLabel(d.next_attempt_at)}`;
  return "";
}

/**
 * Delivery log: search by mobile number and see what that person was sent, on which channel, and
 * whether it arrived.
 * @param {{ phone: string, event: string, cursor: string | null, error: string,
 *   result: { userId: string | null, person: import("./page.jsx").Person | null, items: LogEntry[], next_cursor: string | null } | null }} props
 */
export function DeliveryLogView({ phone, event, cursor, result, error }) {
  const [pending, startTransition] = useTransition();
  const [, setQuery] = useQueryStates(
    { phone: parseAsString, event: parseAsString, cursor: parseAsString },
    { shallow: false, startTransition },
  );
  const [typed, setTyped] = useState(spaced(phone));
  const [chosen, setChosen] = useState(event);

  /** @param {import("react").FormEvent} e */
  function search(e) {
    e.preventDefault();
    const digits = typed.replace(/\D/g, "").replace(/^91(?=\d{10}$)/, "");
    // A user id is searched as typed; anything else is a mobile number.
    const id = typed.trim();
    setQuery({
      phone: /^[0-9a-f-]{36}$/i.test(id) ? id : digits || null,
      event: chosen === "all" ? null : chosen,
      cursor: null,
    });
  }

  const keep = EVENTS.find((e) => e.value === event)?.keep;
  const shown = (result?.items ?? []).filter((e) => !keep || keep(e));

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
      ) : result.userId === null ? (
        <EmptyState
          title="No one with that number"
          body="Check the digits. Only people who have signed up appear here."
        />
      ) : (
        <>
          <p className="text-sm leading-5 text-fg-secondary">
            {result.person
              ? `${result.person.name} · ${result.person.phone_e164}`
              : `User ${result.userId}`}
          </p>
          {shown.length === 0 ? (
            <EmptyState
              title="Nothing sent"
              body={
                result.next_cursor
                  ? "No messages of this kind on this page. Try older messages."
                  : "No messages match this event for this person."
              }
            />
          ) : (
            <div
              aria-busy={pending}
              className={`relative overflow-x-auto rounded-md border border-line ${pending ? "opacity-60" : ""}`}
            >
              <table className="w-full min-w-[760px] border-collapse text-sm leading-5">
                <caption className="sr-only">
                  Messages sent to {result.person?.name ?? "this person"}
                </caption>
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
                  {shown.flatMap(rowsOf).map(({ key, entry, delivery }) => (
                    <tr key={key} className="border-b border-line align-top last:border-b-0">
                      <td className="px-3 py-2.5 whitespace-nowrap">
                        {dayTimeLabel(entry.created_at)}
                      </td>
                      <td className="px-3 py-2.5">{entry.title}</td>
                      <td className="px-3 py-2.5">
                        {delivery ? (CHANNEL[delivery.channel] ?? delivery.channel) : "Inbox only"}
                      </td>
                      <td className="px-3 py-2.5">
                        <code>{entry.kind}</code>
                      </td>
                      <td className="px-3 py-2.5">
                        {delivery && (
                          <>
                            <Badge tone={STATUS[delivery.status]?.tone ?? "neutral"}>
                              {STATUS[delivery.status]?.label ?? delivery.status}
                            </Badge>
                            {note(delivery) && (
                              <div className="text-[13px] leading-[18px] text-fg-secondary">
                                {note(delivery)}
                              </div>
                            )}
                          </>
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
      {result?.userId && (cursor || result.next_cursor) && (
        <div className="flex gap-2">
          {cursor && (
            <Button variant="quiet" isDisabled={pending} onPress={() => setQuery({ cursor: null })}>
              Newest
            </Button>
          )}
          {result.next_cursor && (
            <Button
              variant="quiet"
              isDisabled={pending}
              onPress={() => setQuery({ cursor: result.next_cursor })}
            >
              Older messages
            </Button>
          )}
        </div>
      )}
      <p className="text-sm leading-5 text-fg-secondary">
        Message text is shown in the person’s language, as it was sent.
      </p>
    </>
  );
}
