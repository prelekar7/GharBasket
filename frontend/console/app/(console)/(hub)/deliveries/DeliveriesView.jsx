"use client";

import { Badge, Button, SegmentedControl, TextField, toast } from "@gharbasket/ui";
import { DataTable, FilterBar, KpiTile } from "@gharbasket/ui/console";
import { parseAsString, parseAsStringLiteral, useQueryState } from "nuqs";
import { useMemo, useRef, useState, useTransition } from "react";
import { formatClock, formatDay, formatTime, newKey } from "@/lib/fleet.js";
import { resolveDelivery } from "./actions.js";

/** @typedef {import("./data.js").Delivery} Delivery */

/** The pill each status gets, and what it says. */
const STATUS =
  /** @type {Record<Delivery["status"], ["delivered" | "ready" | "out" | "issue", string]>} */ ({
    scheduled: ["out", "Scheduled"],
    on_route: ["out", "On the van"],
    with_captain: ["out", "With the captain"],
    held: ["ready", "Held by the captain"],
    delivered: ["delivered", "At the door"],
    undelivered: ["issue", "Not delivered"],
    redelivery: ["out", "Redelivery booked"],
    refunded: ["issue", "Refunded"],
    triage: ["issue", "Needs a decision"],
  });

const REASON = /** @type {Record<string, string>} */ ({
  not_home: "Nobody home",
  refused: "Refused",
  wrong_address: "Wrong address",
  damaged: "Damaged",
  missing: "Missing items",
  other: "Other",
  returned_to_hub: "Came back to the hub",
  not_confirmed: "Never confirmed",
});

const PROOF = /** @type {Record<string, string>} */ ({
  scan: "Scanned",
  otp: "Code from the family",
  photo: "Photo at the door",
});
const BY = /** @type {Record<string, string>} */ ({
  captain: "the captain",
  rider: "the rider",
  hub: "the hub",
  system: "the system",
});

const SHOW = [
  { value: "open", label: "Not at the door" },
  { value: "all", label: "All orders" },
];

const COLUMNS = [
  { key: "order", label: "Order", sortable: true },
  { key: "society", label: "Society", sortable: true },
  { key: "status", label: "State" },
  { key: "proof", label: "Proof" },
  { key: "at", label: "Time", sortable: true },
  { key: "next", label: "Next" },
];

/** @param {Record<string, string>} societies @param {string} id */
const named = (societies, id) => societies[id] ?? "A society";

/** An order's short code, the end of its id. @param {string} id */
const shortCode = (id) => id.slice(-6).toUpperCase();

/** Waiting for a hub manager: the API calls the order `undelivered` or `triage`. @param {Delivery} d */
const needsDecision = (d) => d.status === "triage" || d.status === "undelivered";

/** @param {Delivery} d */
function stateText(d) {
  const [, label] = STATUS[d.status];
  const reason = d.reason ? REASON[d.reason] : null;
  if (d.status === "held")
    return d.hold_until ? `${label} until ${formatClock(d.hold_until)}` : label;
  return reason && d.status !== "delivered" ? `${label} · ${reason}` : label;
}

/** @param {Delivery} d */
function proofText(d) {
  if (!d.proof) return d.by ? `By ${BY[d.by]}` : "—";
  return `${PROOF[d.proof]}${d.by ? `, by ${BY[d.by]}` : ""}`;
}

/**
 * Every order's delivery today, filtered to what needs attention by default; a hub manager settles the ones that wait.
 * @param {{ date: string, now: number, items: Delivery[], societies: Record<string, string>, canResolve: boolean }} props
 */
export function DeliveriesView({ date, now, items, societies, canResolve }) {
  const [q, setQ] = useQueryState(
    "q",
    parseAsString.withDefault("").withOptions({ clearOnDefault: true }),
  );
  const [society, setSociety] = useQueryState(
    "society",
    parseAsString.withDefault("all").withOptions({ clearOnDefault: true }),
  );
  const [show, setShow] = useQueryState(
    "show",
    parseAsStringLiteral(["open", "all"]).withDefault("open").withOptions({ clearOnDefault: true }),
  );
  const [resolving, setResolving] = useQueryState("resolve", parseAsString);

  const nameOf = (/** @type {string} */ id) => named(societies, id);
  const societyList = useMemo(
    () =>
      [...new Set(items.map((d) => d.society_id))].map(
        (id) => /** @type {const} */ ([id, named(societies, id)]),
      ),
    [items, societies],
  );
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return items
      .filter((d) => (show === "all" ? true : d.status !== "delivered"))
      .filter((d) => society === "all" || d.society_id === society)
      .filter((d) => !needle || d.order_id.toLowerCase().includes(needle))
      .map((d) => ({
        id: d.order_id,
        __tone: needsDecision(d) ? /** @type {const} */ ("danger") : undefined,
        order: {
          text: shortCode(d.order_id),
          sub: d.attempt === 2 ? "Second attempt" : undefined,
          strong: true,
        },
        society: named(societies, d.society_id),
        status: { status: STATUS[d.status][0], text: stateText(d) },
        proof: proofText(d),
        at: { text: d.at ? formatTime(d.at) : "—", sort: d.at ?? "" },
        next: needsDecision(d)
          ? canResolve
            ? { text: "Decide", href: `?resolve=${d.order_id}` }
            : "A hub manager decides"
          : d.status === "held"
            ? "Collected by the family, or back to the hub"
            : d.status === "with_captain"
              ? "Door scan pending"
              : "—",
      }));
  }, [items, q, society, show, societies, canResolve]);

  const count = (/** @type {Delivery["status"][]} */ ...statuses) =>
    items.filter((d) => statuses.includes(d.status)).length;
  const problems = items.filter((d) => ["undelivered", "triage", "refunded"].includes(d.status));
  const problemHint = problems[0]
    ? `${nameOf(problems[0].society_id)}, ${(REASON[problems[0].reason ?? ""] ?? "see the list").toLowerCase()}${problems.length > 1 ? ` and ${problems.length - 1} more` : ""}`
    : "Nothing to fix";
  const held = items.filter((d) => d.status === "held");
  const holdUntil = held.find((d) => d.hold_until)?.hold_until;
  const withCaptain = societyList
    .map(([id, name]) => ({
      id,
      name,
      orders: items.filter((d) => d.society_id === id && d.status === "with_captain"),
    }))
    .filter((s) => s.orders.length > 0);

  const active = [
    ...(show === "open" ? [{ key: "show", label: "Not at the door" }] : []),
    ...(society !== "all"
      ? [{ key: "society", label: societyList.find(([id]) => id === society)?.[1] ?? "A society" }]
      : []),
  ];
  const sentence = show === "open" ? " (filtered to anything not handed over at the door)" : "";
  const selected = items.find((d) => d.order_id === resolving && needsDecision(d));

  /** Decision links change the URL only; keep the page where it is instead of reloading it. @param {import("react").MouseEvent} e */
  const intercept = (e) => {
    const link = e.target instanceof Element ? e.target.closest('a[href^="?resolve="]') : null;
    if (!link) return;
    e.preventDefault();
    setResolving(new URLSearchParams(link.getAttribute("href") ?? "").get("resolve"));
  };

  return (
    <>
      <div className="flex flex-col gap-0.5">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">
          Deliveries · {formatDay(date)}
        </h1>
        <p className="text-sm leading-5 text-fg-secondary">
          {formatTime(new Date(now).toISOString())}. Every order from handover to the door, with its
          proof. Customer names, flats and phone numbers are never shown here.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-4">
        <KpiTile
          label="At the door"
          value={String(count("delivered"))}
          hint="Delivered with proof"
        />
        <KpiTile
          label="Held by the captain"
          value={String(held.length)}
          hint={holdUntil ? `Collect by ${formatClock(holdUntil)}` : "Nothing held"}
        />
        <KpiTile label="Problems" value={String(problems.length)} hint={problemHint} />
        {withCaptain.map((w) => (
          <KpiTile
            key={w.id}
            label={w.name}
            value={String(w.orders.length)}
            hint="Handed over; captain delivering"
          />
        ))}
      </div>

      <FilterBar
        search={{
          label: "Search deliveries",
          placeholder: "Order code",
          value: q,
          onChange: setQ,
        }}
        filters={[
          { key: "show", label: "Show", value: show, options: SHOW },
          {
            key: "society",
            label: "Society",
            value: society,
            options: [
              { value: "all", label: "All societies" },
              ...societyList.map(([value, label]) => ({ value, label })),
            ],
          },
        ]}
        onFilterChange={(key, value) =>
          key === "show" ? setShow(value === "all" ? "all" : "open") : setSociety(value)
        }
        active={active}
        onRemove={(key) => (key === "show" ? setShow("all") : setSociety("all"))}
        onClear={() => {
          setShow("all");
          setSociety("all");
        }}
        count={rows.length}
        labels={{ results: "{n} orders" }}
      />

      <div
        className={`grid grid-cols-[minmax(0,1fr)] items-start gap-4 ${selected ? "min-[900px]:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]" : ""}`}
      >
        {/* biome-ignore lint/a11y/noStaticElementInteractions: delegates clicks of the links inside the table */}
        <div className="min-w-0" onClick={intercept} onKeyDown={undefined}>
          <DataTable
            columns={COLUMNS}
            rows={rows}
            caption="Orders with their delivery state and proof"
            footer={`${rows.length} of ${items.length} orders shown${sentence}.`}
            labels={{ empty: "No orders match these filters." }}
          />
        </div>
        {selected && (
          <DecisionPanel
            key={selected.order_id}
            delivery={selected}
            society={nameOf(selected.society_id)}
            onDone={() => setResolving(null)}
          />
        )}
      </div>
    </>
  );
}

/**
 * The hub manager's triage of one undelivered order: send it again tomorrow (once) or refund it.
 * @param {{ delivery: Delivery, society: string, onDone: () => void }} props
 */
function DecisionPanel({ delivery: d, society, onDone }) {
  const canRedeliver = d.attempt === 1;
  const [outcome, setOutcome] = useState(canRedeliver ? "redeliver" : "refund");
  const [note, setNote] = useState("");
  const [fields, setFields] = useState(/** @type {Record<string, string>} */ ({}));
  const [pending, start] = useTransition();
  // One Idempotency-Key per decision: retrying the same decision reuses it, a different one gets a new key.
  const attempt = useRef({ body: "", key: "" });

  function decide() {
    if (!note.trim()) {
      setFields({ note: "Write a line for the record" });
      return;
    }
    start(async () => {
      const input = { outcome: /** @type {"redeliver" | "refund"} */ (outcome), note: note.trim() };
      const body = JSON.stringify(input);
      if (attempt.current.body !== body) attempt.current = { body, key: newKey() };
      const r = await resolveDelivery(d.order_id, attempt.current.key, input);
      if (r.ok) {
        toast(
          outcome === "redeliver"
            ? `Order ${shortCode(d.order_id)} goes out again tomorrow.`
            : `Order ${shortCode(d.order_id)} is refunded to the wallet.`,
        );
        onDone();
      } else {
        setFields(r.fields);
        if (r.message) toast(r.message);
      }
    });
  }

  return (
    <section
      aria-label="Decide what happens to this order"
      className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
    >
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[17px] leading-6 font-bold">Order {shortCode(d.order_id)}</h2>
        <Badge tone="danger">{d.reason ? REASON[d.reason] : "Not delivered"}</Badge>
      </div>
      <p className="text-sm leading-5 text-fg-secondary">
        {society}. {d.attempt === 2 ? "This was the free second attempt. " : ""}It could not be
        delivered and is waiting for you.
      </p>
      <SegmentedControl
        label="What to do"
        options={[
          ...(canRedeliver ? [{ value: "redeliver", label: "Send again tomorrow" }] : []),
          { value: "refund", label: "Refund" },
        ]}
        value={outcome}
        onChange={setOutcome}
      />
      <p className="text-sm leading-5 text-fg-secondary">
        {outcome === "redeliver"
          ? "The order rides tomorrow morning at no charge. A second miss is refunded."
          : "The whole amount that is left goes back to the family’s wallet."}
      </p>
      <TextField
        label="Note for the record"
        placeholder="What you agreed with the family or the captain"
        value={note}
        onChange={(v) => {
          setNote(v);
          setFields(({ note: _gone, ...rest }) => rest);
        }}
        isInvalid={!!fields.note}
        errorMessage={fields.note}
      />
      <div className="grid grid-cols-2 gap-2">
        <Button variant="secondary" block isDisabled={pending} onPress={onDone}>
          Not now
        </Button>
        <Button variant="primary" block isDisabled={pending} onPress={decide}>
          {outcome === "redeliver" ? "Send again" : "Refund"}
        </Button>
      </div>
    </section>
  );
}
