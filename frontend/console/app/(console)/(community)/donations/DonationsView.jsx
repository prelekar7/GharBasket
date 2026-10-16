"use client";

import {
  Badge,
  Button,
  Checkbox,
  Dialog,
  EmptyState,
  Select,
  TextField,
  toast,
} from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { useRef, useState, useTransition } from "react";
import { PageHeader, Panel } from "@/components/PageHeader.jsx";
import { Toasts } from "@/components/Toasts.jsx";
import { countProblem, donationPoints } from "@/lib/kindness.js";
import { rejectDonation, verifyDonation } from "./actions.js";

/**
 * GET /v1/admin/donations (M9 section 3.4), as the hub sees it: the donor's tower and flat, never a name or
 * phone. `qty` is what the family said, `counted` what the hub counted once verified.
 * @typedef {import("@/lib/kindness.js").PointRate} PointRate
 * @typedef {{ code: string, unit: "kg" | "each", qty: number, counted: number | null, points: number | null }} Item
 * @typedef {{ id: string, status: "picked_up" | "verified" | "rejected" | string, items: Item[], photos: number,
 *   photo_urls: { url: string, expires_at: string }[], drive: { id: string, name: string, multiplier: number } | null,
 *   pickup_on: string, picked_up_on: string | null, picked_up_by: { name: string } | null,
 *   points_awarded: number | null, society: { id: string, name: string }, tower: string, flat: string }} Donation
 * @typedef {{ id: string, multiplier: number, codes: string[] }} Drive
 * @typedef {import("@/lib/types.js").Column} Column
 */

/** @type {Column[]} */
const COLUMNS = [
  { key: "from", label: "From" },
  { key: "items", label: "Declared" },
  { key: "picked", label: "Picked up" },
  { key: "state", label: "State" },
];

const REASONS = [
  { value: "unusable", label: "Not usable (torn, broken, dirty)" },
  { value: "not_accepted", label: "Not something we accept" },
  { value: "unsafe", label: "Not safe" },
  { value: "other", label: "Something else" },
];

const NUMBER_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];

const dayFmt = new Intl.DateTimeFormat("en-IN", {
  weekday: "short",
  day: "numeric",
  month: "short",
  timeZone: "Asia/Kolkata",
});

/** "Tue 6 Oct" from an ISO date or time. @param {string} iso */
const day = (iso) =>
  dayFmt
    .format(new Date(iso.length === 10 ? `${iso}T06:00:00Z` : iso))
    .replace(",", "")
    .replace("Sept", "Sep");

/** The donor's place as the bag label has it: "Wing C 807". @param {Donation} d */
const place = (d) => (d.tower ? `${d.tower} ${d.flat}` : d.flat);

/** @param {PointRate[]} rates @param {string} code */
const labelOf = (rates, code) => rates.find((r) => r.code === code)?.label ?? code;

/** "Toys ×4 · books ×6", "School bag ×1 · clothes 3 kg". @param {Item[]} items @param {PointRate[]} rates @param {"said" | "counted"} which */
function describe(items, rates, which) {
  return items
    .map((l, i) => {
      const n = which === "said" ? l.qty : (l.counted ?? 0);
      const name = labelOf(rates, l.code);
      const label = i === 0 ? name : name.toLowerCase();
      return l.unit === "kg" ? `${label} ${n} kg` : `${label} ×${n}`;
    })
    .join(" · ");
}

/** "4 toys and 6 books" for the sentence about what the family said. @param {Item[]} items @param {PointRate[]} rates */
function said(items, rates) {
  const parts = items.map((l) =>
    l.unit === "kg"
      ? `${l.qty} kg of ${labelOf(rates, l.code).toLowerCase()}`
      : `${l.qty} ${labelOf(rates, l.code).toLowerCase()}`,
  );
  return parts.length > 1
    ? `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`
    : (parts[0] ?? "");
}

/**
 * The drive's rule for the preview: its multiplier and the categories it multiplies. A drive that is no longer
 * running is not in the list, so its multiplier is applied to every line (the server has the last word).
 * @param {Donation} d @param {Drive[]} drives
 */
function ruleOf(d, drives) {
  if (!d.drive) return null;
  const drive = drives.find((x) => x.id === d.drive?.id);
  return drive ?? { multiplier: d.drive.multiplier, codes: d.items.map((i) => i.code) };
}

/** @param {{ donations: Donation[], rates: PointRate[], drives: Drive[] }} props */
export function DonationsView({ donations, rates, drives }) {
  const [id, setId] = useQueryState("id", parseAsString);
  const queue = donations.filter((d) => d.status === "picked_up");
  const selected = donations.find((d) => d.id === id) ?? queue[0] ?? donations[0];
  const riders = [
    ...new Set(queue.flatMap((d) => (d.picked_up_by ? [d.picked_up_by.name.split(" ")[0]] : []))),
  ];
  const n = queue.length;

  return (
    <>
      <PageHeader title="Donation verification">
        {n > 0
          ? `${riders.join(" and ") || "The van"} brought ${NUMBER_WORDS[n] ?? n} ${n === 1 ? "donation" : "donations"} back on the return leg. `
          : "Nothing is waiting to be verified. "}
        Count what actually came, and the family gets points for that. Nobody is penalised for an
        honest estimate.
      </PageHeader>
      <Toasts />
      {donations.length === 0 ? (
        <EmptyState
          title="No donations yet"
          body="When a family schedules a pickup and the van brings it back, it appears here to count."
        />
      ) : (
        <div className="grid items-start gap-4 min-[1100px]:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          {/* A click on a flat selects its donation without a page load; the href is the no-JS fallback. */}
          {/* biome-ignore lint/a11y/noStaticElementInteractions: delegates clicks of real links inside */}
          {/* biome-ignore lint/a11y/useKeyWithClickEvents: the links inside handle the keyboard */}
          <div
            className="min-w-0"
            onClick={(e) => {
              const a = /** @type {HTMLElement} */ (e.target).closest("a[href^='?id=']");
              if (!a || e.metaKey || e.ctrlKey) return;
              e.preventDefault();
              setId(new URL(/** @type {HTMLAnchorElement} */ (a).href).searchParams.get("id"));
            }}
          >
            <DataTable
              caption="Donations to verify"
              columns={COLUMNS}
              rows={donations.map((d) => ({
                id: d.id,
                from: { text: place(d), sub: d.society.name, strong: true, href: `?id=${d.id}` },
                items: describe(d.items, rates, d.status === "verified" ? "counted" : "said"),
                picked: `${day(d.picked_up_on ?? d.pickup_on)} · ${d.picked_up_by?.name.split(" ")[0] ?? "—"}`,
                state:
                  d.status === "verified"
                    ? { badge: { tone: "primary", label: `Verified · ${d.points_awarded} points` } }
                    : d.status === "rejected"
                      ? { badge: { tone: "outline", label: "Rejected" } }
                      : { badge: { tone: "accent", label: "To verify" } },
              }))}
              footer="Flats only; staff never see the donor’s name or phone here."
            />
          </div>
          {selected && (
            <VerifyPanel
              key={selected.id}
              donation={selected}
              rates={rates}
              rule={ruleOf(selected, drives)}
              onDone={() => setId(null)}
            />
          )}
        </div>
      )}
    </>
  );
}

/** @param {{ donation: Donation, rates: PointRate[], rule: ReturnType<typeof ruleOf>, onDone: () => void }} props */
function VerifyPanel({ donation: d, rates, rule, onDone }) {
  const open = d.status === "picked_up";
  const [counted, setCounted] = useState(
    /** @type {Record<string, string>} */ (
      Object.fromEntries(d.items.map((l) => [l.code, String(l.counted ?? l.qty)]))
    ),
  );
  const [unsuitable, setUnsuitable] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("unusable");
  const [pending, start] = useTransition();
  const attempt = useRef(crypto.randomUUID());
  const rejectAttempt = useRef(crypto.randomUUID());

  const numbers = Object.fromEntries(
    d.items.map((l) => [
      l.code,
      counted[l.code]?.trim() === "" ? Number.NaN : Number(counted[l.code]),
    ]),
  );
  /** @param {string} code */
  const problemOf = (code) => {
    const rate = rates.find((r) => r.code === code);
    return rate ? countProblem(rate, numbers[code]) : null;
  };
  const invalid = d.items.filter((l) => problemOf(l.code));
  const preview = donationPoints(
    Object.fromEntries(Object.entries(numbers).map(([k, v]) => [k, Number.isNaN(v) ? 0 : v])),
    rates,
    rule,
  );
  const base = donationPoints(
    Object.fromEntries(Object.entries(numbers).map(([k, v]) => [k, Number.isNaN(v) ? 0 : v])),
    rates,
    null,
  ).total;
  const total = preview.total;
  const nothing = !Object.values(numbers).some((n) => n > 0);
  const counts = describe(
    d.items.map((l) => ({ ...l, counted: numbers[l.code] })),
    rates,
    "counted",
  ).toLowerCase();

  function verify() {
    start(async () => {
      const res = await verifyDonation(
        d.id,
        { counted: numbers, has_unsuitable: unsuitable },
        attempt.current,
      );
      if (!res.ok) {
        toast(res.message);
        return;
      }
      attempt.current = crypto.randomUUID();
      toast(`${place(d)} verified: ${res.points} points given`);
      onDone();
    });
  }

  function reject() {
    start(async () => {
      const res = await rejectDonation(
        d.id,
        /** @type {"unusable" | "not_accepted" | "unsafe" | "other"} */ (reason),
        rejectAttempt.current,
      );
      if (!res.ok) {
        toast(res.message);
        return;
      }
      rejectAttempt.current = crypto.randomUUID();
      toast(`${place(d)} rejected. The family earns no points for it.`);
      onDone();
    });
  }

  return (
    <Panel
      title={`${d.society.name} · ${place(d)}`}
      label="Verify donation"
      aside={
        d.drive ? (
          <Badge tone="accent">{`${d.drive.name} · ${d.drive.multiplier}×`}</Badge>
        ) : undefined
      }
    >
      <p className="text-sm leading-5 text-fg-secondary">
        The family said: {said(d.items, rates)}. {d.photos} {d.photos === 1 ? "photo" : "photos"}
        {d.photo_urls.length > 0 && (
          <>
            {" ("}
            {d.photo_urls.map((p, i) => (
              <span key={p.url + String(i)}>
                {i > 0 && ", "}
                <a href={p.url} target="_blank" rel="noreferrer" className="underline">
                  open {i + 1}
                </a>
              </span>
            ))}
            {")"}
          </>
        )}
        .
      </p>
      {d.items.map((l) => {
        const bad = invalid.includes(l);
        const pts = bad ? 0 : (l.points ?? preview.lines[l.code] ?? 0);
        return (
          <div key={l.code} className="grid grid-cols-[minmax(0,1fr)_96px_56px] items-start gap-3">
            <span className="pt-6">
              <strong>{labelOf(rates, l.code)}</strong>
              <span className="block text-sm text-fg-secondary">
                said {l.qty}
                {l.unit === "kg" ? " kg" : ""}
              </span>
            </span>
            <TextField
              label={l.unit === "kg" ? "Counted (kg)" : "Counted"}
              inputMode="decimal"
              value={counted[l.code]}
              isReadOnly={!open}
              isInvalid={bad}
              errorMessage={problemOf(l.code) ?? undefined}
              onChange={(v) => setCounted((c) => ({ ...c, [l.code]: v }))}
              className="min-w-0"
            />
            <strong className="pt-[30px] text-right tabular-nums">{pts}</strong>
          </div>
        );
      })}
      <div className="flex items-center justify-between gap-3 border-t border-line pt-3">
        <span className="text-sm text-fg-secondary">
          {!open
            ? d.status === "verified"
              ? "Awarded"
              : "Earned"
            : d.drive
              ? `Base ${base}, then the ${d.drive.name}’s ${d.drive.multiplier}×`
              : `Base ${base}`}
        </span>
        <strong className="text-xl tabular-nums">
          {open ? total : (d.points_awarded ?? 0)} points
        </strong>
      </div>
      {!open ? (
        <p className="text-sm leading-5 text-fg-secondary">
          {d.status === "verified"
            ? "Verified. The family has these points in their gullak."
            : "Rejected. The family earned no points for this one."}
        </p>
      ) : (
        <>
          <Checkbox
            label="Not suitable to donate"
            description="Torn or unusable items go to recycling; the family is told kindly, with no points lost for the rest."
            isSelected={unsuitable}
            onChange={setUnsuitable}
          />
          <Button
            variant="primary"
            block
            isDisabled={pending || invalid.length > 0 || nothing}
            onPress={() => setConfirm(true)}
          >
            {`Verify and give ${total} points`}
          </Button>
          <Button variant="quiet" block isDisabled={pending} onPress={() => setRejecting(true)}>
            Reject this donation
          </Button>
        </>
      )}
      <Dialog
        title={`Give ${total} points?`}
        confirmLabel={`Give ${total} points`}
        cancelLabel="Not yet"
        isOpen={confirm}
        onOpenChange={setConfirm}
        onConfirm={verify}
      >
        {place(d)} gets {total} points for {counts}. Points can’t be taken back once given.
      </Dialog>
      <Dialog
        title="Reject this donation?"
        confirmLabel="Reject"
        cancelLabel="Not yet"
        isOpen={rejecting}
        onOpenChange={setRejecting}
        onConfirm={reject}
      >
        <p>It earns no points and the items are not returned; unusable things go to a recycler.</p>
        <Select label="Why" options={REASONS} value={reason} onChange={setReason} />
      </Dialog>
    </Panel>
  );
}
