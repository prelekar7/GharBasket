"use client";

import { Badge, Button, Checkbox, Dialog, EmptyState, TextField, toast } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { useRef, useState, useTransition } from "react";
import { PageHeader, Panel } from "@/components/PageHeader.jsx";
import { Toasts } from "@/components/Toasts.jsx";
import { verifyDonation } from "./actions.js";

/** @typedef {import("@/mocks/community.js").Donation} Donation */
/** @typedef {import("@/mocks/community.js").DonationLine} DonationLine */
/** @typedef {import("@/lib/types.js").Column} Column */

/** @type {Column[]} */
const COLUMNS = [
  { key: "from", label: "From" },
  { key: "items", label: "Declared" },
  { key: "picked", label: "Picked up" },
  { key: "state", label: "State" },
];

const NUMBER_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];

const dayFmt = new Intl.DateTimeFormat("en-IN", {
  weekday: "short",
  day: "numeric",
  month: "short",
  timeZone: "Asia/Kolkata",
});
const timeFmt = new Intl.DateTimeFormat("en-IN", {
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
  timeZone: "Asia/Kolkata",
});

/** "Tue 6 Oct" from an ISO date or time. @param {string} iso */
const day = (iso) =>
  dayFmt
    .format(new Date(iso.length === 10 ? `${iso}T06:00:00Z` : iso))
    .replace(",", "")
    .replace("Sept", "Sep");

/** "Toys ×4 · books ×6", "School bag ×1 · clothes 3 kg". @param {DonationLine[]} lines @param {"said" | "counted"} which */
function describe(lines, which) {
  return lines
    .map((l, i) => {
      const n = which === "said" ? l.said : (l.counted ?? 0);
      const label = i === 0 ? l.label : l.label.toLowerCase();
      return l.unit === "kg" ? `${label} ${n} kg` : `${label} ×${n}`;
    })
    .join(" · ");
}

/** "toys" style for the sentence "4 toys and 6 books". @param {DonationLine[]} lines */
function said(lines) {
  const parts = lines.map((l) =>
    l.unit === "kg"
      ? `${l.said} kg of ${l.label.toLowerCase()}`
      : `${l.said} ${l.label.toLowerCase()}`,
  );
  return parts.length > 1
    ? `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`
    : (parts[0] ?? "");
}

/** Base points of the counted quantities, rounded per line like the server does. @param {DonationLine[]} lines @param {Record<string, number>} counted */
const basePoints = (lines, counted) =>
  lines.reduce((sum, l) => sum + Math.round((counted[l.key] ?? 0) * l.rate), 0);

/** @param {{ donations: Donation[], asOf: string }} props */
export function DonationsView({ donations, asOf }) {
  const [id, setId] = useQueryState("id", parseAsString);
  const queue = donations.filter((d) => d.status === "to_verify");
  const selected = donations.find((d) => d.id === id) ?? queue[0] ?? donations[0];
  const riders = [...new Set(queue.map((d) => d.picked_up_by.split(" ")[0]))];
  const n = queue.length;

  return (
    <>
      <PageHeader title="Donation verification">
        {day(asOf)}, {timeFmt.format(new Date(asOf)).toUpperCase()}.{" "}
        {n > 0
          ? `${riders.join(" and ")} brought ${NUMBER_WORDS[n] ?? n} ${n === 1 ? "donation" : "donations"} back on the return leg. `
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
                from: { text: d.flat, sub: d.society, strong: true, href: `?id=${d.id}` },
                items: describe(d.lines, d.status === "verified" ? "counted" : "said"),
                picked: `${day(d.picked_up_on)} · ${d.picked_up_by.split(" ")[0]}`,
                state:
                  d.status === "verified"
                    ? { badge: { tone: "primary", label: `Verified · ${d.points_awarded} points` } }
                    : { badge: { tone: "accent", label: "To verify" } },
              }))}
              footer="Flats only; staff never see the donor’s name or phone here."
            />
          </div>
          {selected && (
            <VerifyPanel key={selected.id} donation={selected} onDone={() => setId(null)} />
          )}
        </div>
      )}
    </>
  );
}

/** @param {{ donation: Donation, onDone: () => void }} props */
function VerifyPanel({ donation: d, onDone }) {
  const verified = d.status === "verified";
  const [counted, setCounted] = useState(
    /** @type {Record<string, string>} */ (
      Object.fromEntries(d.lines.map((l) => [l.key, String(l.counted ?? l.said)]))
    ),
  );
  const [unsuitable, setUnsuitable] = useState(d.has_unsuitable);
  const [confirm, setConfirm] = useState(false);
  const [pending, start] = useTransition();
  const attempt = useRef(crypto.randomUUID());

  const numbers = Object.fromEntries(d.lines.map((l) => [l.key, Number(counted[l.key])]));
  const invalid = d.lines.filter((l) => !(counted[l.key]?.trim() !== "" && numbers[l.key] >= 0));
  const base = basePoints(d.lines, numbers);
  const total = Math.round(base * d.drive.multiplier);
  const mult = `${d.drive.multiplier}×`;
  const counts = describe(
    d.lines.map((l) => ({ ...l, counted: numbers[l.key] })),
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
      toast(`${d.flat} verified: ${res.points} points given`);
      onDone();
    });
  }

  return (
    <Panel
      title={`${d.society} · ${d.flat}`}
      label="Verify donation"
      aside={<Badge tone="accent">{`${d.drive.name} · ${mult}`}</Badge>}
    >
      <p className="text-sm leading-5 text-fg-secondary">
        The family said: {said(d.lines)}. {d.photos} {d.photos === 1 ? "photo" : "photos"}.
      </p>
      {d.lines.map((l) => {
        const bad = invalid.includes(l);
        const pts = bad ? 0 : Math.round(numbers[l.key] * l.rate);
        return (
          <div key={l.key} className="grid grid-cols-[minmax(0,1fr)_96px_56px] items-start gap-3">
            <span className="pt-6">
              <strong>{l.label}</strong>
              <span className="block text-sm text-fg-secondary">
                said {l.said}
                {l.unit === "kg" ? " kg" : ""}
              </span>
            </span>
            <TextField
              label={l.unit === "kg" ? "Counted (kg)" : "Counted"}
              inputMode="decimal"
              value={counted[l.key]}
              isReadOnly={verified}
              isInvalid={bad}
              errorMessage="0 or more"
              onChange={(v) => setCounted((c) => ({ ...c, [l.key]: v }))}
              className="min-w-0"
            />
            <strong className="pt-[30px] text-right tabular-nums">{pts}</strong>
          </div>
        );
      })}
      <div className="flex items-center justify-between gap-3 border-t border-line pt-3">
        <span className="text-sm text-fg-secondary">
          {verified ? "Awarded" : `Base ${base} × ${d.drive.multiplier} for the ${d.drive.name}`}
        </span>
        <strong className="text-xl tabular-nums">
          {verified ? d.points_awarded : total} points
        </strong>
      </div>
      {verified ? (
        <p className="text-sm leading-5 text-fg-secondary">
          Verified. The family has these points in their gullak.
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
            isDisabled={pending || invalid.length > 0}
            onPress={() => setConfirm(true)}
          >
            {`Verify and give ${total} points`}
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
        {d.flat} gets {total} points for {counts}. Points can’t be taken back once given.
      </Dialog>
    </Panel>
  );
}
