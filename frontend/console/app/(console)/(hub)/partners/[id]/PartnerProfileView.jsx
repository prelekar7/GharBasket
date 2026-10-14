"use client";

import {
  Badge,
  Button,
  buttonStyles,
  Dialog,
  KnowYourCookCard,
  TextField,
  toast,
} from "@gharbasket/ui";
import { DataTable, KpiTile } from "@gharbasket/ui/console";
import Link from "next/link";
import { useRef, useState, useTransition } from "react";
import { newKey } from "@/lib/fleet.js";
import { Toasts } from "../../_fleet/Toasts.jsx";
import { suspendPartner } from "./actions.js";

/** @param {{ partner: import("./data.js").PartnerProfile }} props */
export function PartnerProfileView({ partner: p }) {
  const suspended = p.status === "suspended";
  const rows = p.payouts.items.map((w) => ({
    id: w.week,
    week: w.week,
    units: String(w.units),
    paid: { money: w.paid_paise },
  }));
  const first = p.name.split(" ")[0];
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
          <h1 className="font-display text-[28px] leading-[34px] font-semibold">{p.name}</h1>
          <p className="text-sm leading-5 text-fg-secondary">
            {[
              p.kind_label,
              p.hub.name,
              suspended ? "suspended" : "verified",
              p.language && `speaks ${p.language}`,
            ]
              .filter(Boolean)
              .join(" · ")}
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
          {suspended ? <Badge tone="danger">Suspended</Badge> : <SuspendButton partner={p} />}
        </div>
      </div>
      {suspended && (
        <p
          role="status"
          className="rounded-sm border border-danger bg-surface p-3 text-sm leading-5"
        >
          {first} is suspended and gets no new assignments.
          {p.suspended_reason ? ` Reason on record: ${p.suspended_reason}` : ""}
        </p>
      )}

      {p.stats.length > 0 && (
        <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-4">
          {p.stats.map((k) => (
            <KpiTile key={k.label} label={k.label} value={k.value} hint={k.hint} />
          ))}
        </div>
      )}

      <div
        className={`grid grid-cols-[minmax(0,1fr)] items-start gap-4 ${p.public_card ? "min-[900px]:grid-cols-3" : "min-[900px]:grid-cols-2"}`}
      >
        {p.public_card && (
          <section className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4">
            <h2 className="text-[17px] leading-6 font-bold">What families see</h2>
            <KnowYourCookCard
              name={p.name}
              kitchen={p.public_card.kitchen}
              specialty={p.public_card.specialty}
              fssai={p.public_card.fssai}
              hygiene={p.public_card.hygiene}
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
            {p.facts.map((f) => (
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
              { key: "units", label: p.payouts.unit_label, align: "end" },
              { key: "paid", label: "Paid", align: "end" },
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

/** @param {{ partner: import("./data.js").PartnerProfile }} props */
function SuspendButton({ partner: p }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [, start] = useTransition();
  // One Idempotency-Key per suspension; a fresh one each time the dialog opens.
  const key = useRef("");

  function confirm() {
    start(async () => {
      const r = await suspendPartner(p.id, key.current, reason.trim());
      toast(r.ok ? `${p.name} is suspended. They get no new assignments.` : r.message);
    });
  }

  return (
    <>
      <Button
        variant="danger"
        onPress={() => {
          key.current = newKey();
          setReason("");
          setOpen(true);
        }}
      >
        Suspend
      </Button>
      <Dialog
        title={`Suspend ${p.name}?`}
        confirmLabel="Suspend"
        cancelLabel="Keep active"
        tone="danger"
        isOpen={open}
        onOpenChange={setOpen}
        onConfirm={confirm}
      >
        <div className="flex flex-col gap-3">
          <p>
            They stop receiving assignments and their earnings for new work pause until a hub
            manager or admin reinstates them.
          </p>
          <TextField
            label="Reason (optional)"
            description="Kept in the audit log, not shown to them."
            value={reason}
            onChange={setReason}
          />
        </div>
      </Dialog>
    </>
  );
}
