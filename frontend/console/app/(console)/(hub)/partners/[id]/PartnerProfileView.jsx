"use client";

import {
  Button,
  buttonStyles,
  Dialog,
  KnowYourCookCard,
  Select,
  TextField,
  toast,
} from "@gharbasket/ui";
import { DataTable, KpiTile } from "@gharbasket/ui/console";
import Link from "next/link";
import { useRef, useState, useTransition } from "react";
import { formatDate, newKey, rupees } from "@/lib/fleet.js";
import { KIND_LABEL, SUSPEND_REASONS, suspendReasonLabel } from "@/lib/partners.js";
import { weekRange } from "@/lib/payout-weeks.js";
import { Toasts } from "../../_fleet/Toasts.jsx";
import { recordInspection, reinstatePartner, suspendPartner } from "./actions.js";

const SKILL = /** @type {Record<string, string>} */ ({
  veg: "Vegetarian",
  jain: "Jain",
  no_onion_garlic: "No onion or garlic",
  diabetic: "Diabetic-friendly",
  egg: "Egg",
  nonveg: "Non-vegetarian",
});

const PAYOUT_STATE = /** @type {Record<string, string>} */ ({
  processed: "Paid",
  processing: "Sending",
  pending: "Queued",
  held: "Held",
  failed: "Failed",
});

const GRADES = [
  { value: "A", label: "A · very good" },
  { value: "B", label: "B · good" },
  { value: "C", label: "C · needs work" },
];

/** "2026-09" → "Sep 2026". @param {string} month */
const monthLabel = (month) => `${formatDate(`${month}-01`).split(" ")[1]} ${month.slice(0, 4)}`;

/** @param {{ data: Awaited<ReturnType<import("./data.js").getPartner>> }} props */
export function PartnerProfileView({ data: { partner: p, hubName, card, payouts } }) {
  const suspended = p.status === "suspended";
  const name = p.name ?? p.display_name;
  const first = name.split(" ")[0];
  const rows = payouts.map((w) => ({
    id: w.week,
    week: { text: weekRange(w.period), sub: w.week },
    state: PAYOUT_STATE[w.status] ?? w.status,
    paid: { money: w.amount_paise },
  }));
  const lastPaid = payouts.find((w) => w.status === "processed");
  const facts = [
    { k: "Shown to families as", v: p.display_name },
    { k: "Phone", v: p.phone_e164 ?? "—" },
    ...(p.requested_society ? [{ k: "Society", v: p.requested_society.name }] : []),
    { k: "Application", v: p.application_id ? "From the app" : "Added by GharBasket" },
    ...(card
      ? [
          { k: "Area", v: card.area },
          {
            k: "FSSAI registration",
            v: `${card.fssai_reg_no} · valid to ${formatDate(card.fssai_valid_until)}`,
          },
          {
            k: "Hygiene grade",
            v: card.hygiene
              ? `${card.hygiene.grade} · inspected ${formatDate(card.hygiene.inspected_on)}`
              : "Not inspected yet",
          },
          { k: "Skills", v: card.skills.map((s) => SKILL[s] ?? s).join(", ") || "—" },
          { k: "Tiffins delivered", v: String(card.portions_served) },
        ]
      : []),
  ];
  const stats = [
    {
      label: "Status",
      value:
        p.status === "active"
          ? "Active"
          : p.status === "paused"
            ? "Paused"
            : suspended
              ? "Suspended"
              : "Left",
      hint:
        suspended && p.suspension
          ? suspendReasonLabel(p.suspension.reason_code)
          : `Verified ${formatDate(p.verified_at)}`,
    },
    {
      label: "Families’ rating",
      value: p.rating ? p.rating.average.toFixed(1) : "—",
      hint: p.rating
        ? `${p.rating.count} ratings`
        : p.kind === "kitchen"
          ? "Shown from 5 ratings"
          : "Only kitchens are rated",
    },
    {
      label: "Last payout",
      value: lastPaid ? rupees(lastPaid.amount_paise) : "—",
      hint: lastPaid ? weekRange(lastPaid.period) : "None paid yet",
    },
  ];

  return (
    <>
      <Link
        href="/partners"
        className="w-fit rounded-xs text-sm text-link underline outline-none focus-visible:shadow-focus"
      >
        ← Partners and KYC
      </Link>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">{name}</h1>
          <p className="text-sm leading-5 text-fg-secondary">
            {[KIND_LABEL[p.kind], hubName, suspended ? "suspended" : "verified"].join(" · ")}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <a
            href={`/partners/${p.id}/income-certificate`}
            target="_blank"
            rel="noopener"
            className={buttonStyles({ variant: "secondary" })}
          >
            Download income certificate
          </a>
          {card && !suspended && <InspectionButton partner={p} grade={card.hygiene?.grade} />}
          {suspended ? <ReinstateButton partner={p} /> : <SuspendButton partner={p} />}
        </div>
      </div>
      {suspended && (
        <p
          role="status"
          className="rounded-sm border border-danger bg-surface p-3 text-sm leading-5"
        >
          {first} is suspended and gets no new assignments.
          {p.suspension
            ? ` Reason on record: ${suspendReasonLabel(p.suspension.reason_code)}${p.suspension.note ? `, ${p.suspension.note}` : ""}`
            : ""}
        </p>
      )}

      <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-3">
        {stats.map((k) => (
          <KpiTile key={k.label} label={k.label} value={k.value} hint={k.hint} />
        ))}
      </div>

      <div
        className={`grid grid-cols-[minmax(0,1fr)] items-start gap-4 ${card ? "min-[900px]:grid-cols-3" : "min-[900px]:grid-cols-2"}`}
      >
        {card && (
          <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
            <h2 className="text-[17px] leading-6 font-bold">What families see</h2>
            <KnowYourCookCard
              name={card.display_name}
              kitchen={card.area}
              since={monthLabel(card.since)}
              specialty={card.skills.map((s) => SKILL[s] ?? s).join(", ")}
              fssai={!!card.fssai_reg_no}
              hygiene={!!card.hygiene}
              labels={card.hygiene ? { hygiene: `Hygiene grade ${card.hygiene.grade}` } : undefined}
            />
            <p className="text-sm leading-5 text-fg-secondary">
              Shown on every tiffin this kitchen cooks. No phone number or address.
            </p>
          </section>
        )}
        <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
          <h2 className="text-[17px] leading-6 font-bold">
            {p.kind === "kitchen" ? "Kitchen" : "Details"}
          </h2>
          <dl className="m-0">
            {facts.map((f) => (
              <div
                key={f.k}
                className="flex justify-between gap-3 border-b border-line py-1.5 text-sm"
              >
                <dt className="text-fg-secondary">{f.k}</dt>
                <dd className="m-0 text-right font-bold">{f.v}</dd>
              </div>
            ))}
          </dl>
        </section>
        <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
          <h2 className="text-[17px] leading-6 font-bold">Recent payouts</h2>
          <DataTable
            dense
            caption="Recent payouts"
            columns={[
              { key: "week", label: "Week" },
              { key: "state", label: "Payout" },
              { key: "paid", label: "Amount", align: "end" },
            ]}
            rows={rows}
            labels={{ empty: "No payouts yet." }}
          />
          <p className="text-sm leading-5 text-fg-secondary">
            From the ledger; every rupee is a double entry.
          </p>
        </section>
      </div>
      <Toasts />
    </>
  );
}

/** @param {{ partner: import("@/lib/partners.js").PartnerStaff }} props */
function SuspendButton({ partner: p }) {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [note, setNote] = useState("");
  const [fields, setFields] = useState(/** @type {Record<string, string>} */ ({}));
  const [problem, setProblem] = useState("");
  const [pending, start] = useTransition();
  // One Idempotency-Key per suspension; a fresh one each time the form opens.
  const key = useRef("");

  function confirm() {
    start(async () => {
      const r = await suspendPartner(p.id, key.current, { reason_code: code, note: note.trim() });
      if (r.ok) {
        toast(`${p.name ?? p.display_name} is suspended. They get no new assignments.`);
        setOpen(false);
      } else {
        setFields(r.fields);
        setProblem(r.message);
      }
    });
  }

  if (!open) {
    return (
      <Button
        variant="danger"
        onPress={() => {
          key.current = newKey();
          setCode("");
          setNote("");
          setFields({});
          setProblem("");
          setOpen(true);
        }}
      >
        Suspend
      </Button>
    );
  }
  return (
    <form
      aria-label="Suspend partner"
      onSubmit={(e) => {
        e.preventDefault();
        confirm();
      }}
      className="flex w-full min-w-0 flex-col gap-3 rounded-md border border-danger bg-surface p-4"
    >
      <h2 className="text-[17px] leading-6 font-bold">Suspend {p.name ?? p.display_name}?</h2>
      <p className="text-sm leading-5 text-fg-secondary">
        Their role stops at once and tiffins they were offered go to the next round. They stay
        suspended until a hub manager or admin reinstates them.
      </p>
      <Select
        label="Reason"
        placeholder="Pick a reason"
        options={SUSPEND_REASONS}
        value={code}
        onChange={(v) => {
          setCode(v);
          setFields(({ reason_code: _gone, ...rest }) => rest);
        }}
        error={fields.reason_code}
      />
      <TextField
        label="What happened"
        description="Kept in the audit log, not shown to them."
        value={note}
        onChange={(v) => {
          setNote(v);
          setFields(({ note: _gone, ...rest }) => rest);
        }}
        isInvalid={!!fields.note}
        errorMessage={fields.note}
      />
      {problem && (
        <p role="alert" className="text-sm leading-5 font-semibold text-danger">
          {problem}
        </p>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button onPress={() => setOpen(false)}>Keep active</Button>
        <Button type="submit" variant="danger" isDisabled={pending}>
          Suspend
        </Button>
      </div>
    </form>
  );
}

/** @param {{ partner: import("@/lib/partners.js").PartnerStaff }} props */
function ReinstateButton({ partner: p }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [, start] = useTransition();
  const key = useRef("");

  function confirm() {
    start(async () => {
      const r = await reinstatePartner(p.id, key.current, note.trim());
      toast(
        r.ok
          ? p.kind === "captain"
            ? `${p.name ?? p.display_name} is reinstated. Appoint her to her society again.`
            : `${p.name ?? p.display_name} is reinstated.`
          : r.message || (r.fields.note ?? "That did not work. Try again."),
      );
    });
  }

  return (
    <>
      <Button
        variant="primary"
        onPress={() => {
          key.current = newKey();
          setNote("");
          setOpen(true);
        }}
      >
        Reinstate
      </Button>
      <Dialog
        title={`Reinstate ${p.name ?? p.display_name}?`}
        confirmLabel="Reinstate"
        cancelLabel="Keep suspended"
        isOpen={open}
        onOpenChange={setOpen}
        onConfirm={confirm}
      >
        <div className="flex flex-col gap-3">
          <p>
            They get their role back and can receive assignments again.
            {p.kind === "captain"
              ? " A captain is not re-appointed to her society automatically."
              : ""}
          </p>
          <TextField
            label="Note (optional)"
            description="Kept in the audit log, not shown to them."
            value={note}
            onChange={setNote}
          />
        </div>
      </Dialog>
    </>
  );
}

/** @param {{ partner: import("@/lib/partners.js").PartnerStaff, grade?: string }} props */
function InspectionButton({ partner: p, grade: current }) {
  const [open, setOpen] = useState(false);
  const [grade, setGrade] = useState(current ?? "A");
  const [note, setNote] = useState("");
  const [, start] = useTransition();
  const key = useRef("");

  function confirm() {
    start(async () => {
      const r = await recordInspection(p.id, key.current, { grade, note: note.trim() });
      toast(
        r.ok
          ? `Inspection recorded: grade ${grade}.`
          : r.message || "That did not work. Try again.",
      );
    });
  }

  return (
    <>
      <Button
        onPress={() => {
          key.current = newKey();
          setGrade(current ?? "A");
          setNote("");
          setOpen(true);
        }}
      >
        Record inspection
      </Button>
      <Dialog
        title={`Record an inspection of ${p.display_name}’s kitchen?`}
        confirmLabel="Save grade"
        cancelLabel="Cancel"
        isOpen={open}
        onOpenChange={setOpen}
        onConfirm={confirm}
      >
        <div className="flex flex-col gap-3">
          <p>The grade shows on the kitchen’s card to every family that books her tiffins.</p>
          <Select label="Hygiene grade" options={GRADES} value={grade} onChange={setGrade} />
          <TextField label="Note (optional)" value={note} onChange={setNote} />
        </div>
      </Dialog>
    </>
  );
}
