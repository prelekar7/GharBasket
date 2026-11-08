"use client";

import { Button, EmptyState, MoneyText, Sheet, TextField, toast } from "@gharbasket/ui";
import { useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { StatTile } from "@/components/ops/StatTile.jsx";
import { dayTimeLabel } from "@/lib/when.js";
import { resolveMismatch } from "./actions.js";

/** The note's length in the contract (ResolveIn): 1 to 500 characters. */
const NOTE_MAX = 500;

const th = "border-b border-line-strong px-3 py-2.5 font-semibold whitespace-nowrap";

/** What each check found (M5 §3.4), in the page's words. @type {Record<import("./page.jsx").ReconKind, string>} */
const KIND = {
  captured_not_credited: "Paid at Razorpay, but the wallet wasn’t credited",
  credited_not_captured: "Wallet credited, but Razorpay has no captured payment",
  amount_mismatch: "Amounts differ",
  unknown_payment: "A captured payment for an order that isn’t ours",
  refund_status_mismatch: "The refund’s status differs from Razorpay’s",
  refund_unknown: "A Razorpay refund with no entry in our ledger",
  refund_stuck: "A refund still pending after 7 days",
};

/** The opening sentence about the hourly job's last run. @param {import("./page.jsx").ReconRun | null} run */
function lastRunLine(run) {
  if (!run) return "No check has run yet.";
  const when = dayTimeLabel(run.scheduled_for);
  if (run.status === "skipped")
    return `The last check (${when}) was skipped: there are no Razorpay keys, so dev top-ups aren’t checked.`;
  if (run.status === "failed")
    return `The last check (${when}) failed: Razorpay was unavailable, and nothing was changed.`;
  return `Last check ${when}.`;
}

/** A nullable amount, or a dash that says what is missing. @param {{ paise: number | null, missing: string }} props */
function Amount({ paise, missing }) {
  if (paise == null)
    return (
      <>
        <span aria-hidden>—</span>
        <span className="sr-only">{missing}</span>
      </>
    );
  return <MoneyText paise={paise} size="sm" />;
}

/**
 * Razorpay reconciliation: counts of what matched and a table of what didn't, with the fix for each.
 * @param {{ report: import("./page.jsx").ReconPage }} props
 */
export function ReconciliationView({ report }) {
  const { items, last_run: run } = report;
  const [pending, startTransition] = useTransition();
  // The item being resolved, and the note for it. Not saved until "Resolve with note".
  const [look, setLook] = useState(/** @type {import("./page.jsx").ReconItem | null} */ (null));
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState("");

  /** @param {import("./page.jsx").ReconItem | null} item */
  function open(item) {
    setLook(item);
    setNote("");
    setProblem("");
  }

  function resolve() {
    if (!look) return;
    const item = look;
    const text = note.trim();
    if (!text) return setProblem("Write what you decided. It is saved in the audit log.");
    if (text.length > NOTE_MAX) return setProblem(`Keep the note under ${NOTE_MAX} characters.`);
    startTransition(async () => {
      const r = await resolveMismatch(item.id, text);
      if (r.ok) {
        toast(`${item.razorpay_ref} is resolved.`);
        open(null);
      } else setProblem(r.fields.note ?? r.message);
    });
  }

  return (
    <>
      <PageHead title="Reconciliation with Razorpay">
        Every hour, the Razorpay payments and refunds of the last 72 hours, and every item still
        open, are checked against our ledger. {lastRunLine(run)}
      </PageHead>

      <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-4">
        <StatTile
          danger={items.length > 0}
          label="Open now"
          value={report.next_cursor ? `${items.length}+` : items.length}
        />
        <StatTile label="Checked in the last run" value={run?.checked ?? "—"} />
        <StatTile
          label="Healed by the last run"
          value={run?.healed ?? "—"}
          note="Lost webhooks the job repaired itself"
        />
        <StatTile
          danger={(run?.opened ?? 0) > 0}
          label="Opened by the last run"
          value={run?.opened ?? "—"}
        />
      </div>

      {items.length === 0 ? (
        <EmptyState
          title="Nothing open"
          body="Every Razorpay payment and refund the check looked at matches our ledger."
        />
      ) : (
        <div className="relative overflow-x-auto rounded-md border border-line">
          <table className="w-full min-w-[820px] border-collapse text-sm leading-5">
            <caption className="sr-only">Payments that don’t match the ledger</caption>
            <thead>
              <tr className="text-left text-fg-secondary">
                <th scope="col" className={th}>
                  Razorpay reference
                </th>
                <th scope="col" className={th}>
                  First seen
                </th>
                <th scope="col" className={`${th} text-right`}>
                  At Razorpay
                </th>
                <th scope="col" className={`${th} text-right`}>
                  In our ledger
                </th>
                <th scope="col" className={th}>
                  Problem
                </th>
                <th scope="col" className={th}>
                  <span className="sr-only">Fix</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((m) => (
                <tr key={m.id} className="border-b border-line align-middle last:border-b-0">
                  <td className="px-3 py-2.5">
                    <code>{m.razorpay_ref}</code>
                  </td>
                  <td className="px-3 py-2.5 whitespace-nowrap">{dayTimeLabel(m.first_seen_at)}</td>
                  <td className="px-3 py-2.5 text-right">
                    <Amount paise={m.actual_paise} missing="Nothing at Razorpay" />
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <Amount paise={m.expected_paise} missing="Nothing in the ledger" />
                  </td>
                  <td className="px-3 py-2.5">{KIND[m.kind]}</td>
                  <td className="px-3 py-2.5 text-right">
                    <Button onPress={() => open(m)}>Resolve</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-sm leading-5 text-fg-secondary">
        Resolving closes an item with your note and moves no money. A money correction is a
        separate, manual adjustment entry; nothing in the ledger is edited or deleted. Each
        resolution is saved to the audit log with your name.
      </p>

      <Sheet
        title="Resolve this item"
        description={
          look ? `${look.razorpay_ref} · first seen ${dayTimeLabel(look.first_seen_at)}` : undefined
        }
        open={look !== null}
        onOpenChange={(o) => !o && open(null)}
      >
        {look && (
          <form
            aria-label="Resolve with a note"
            onSubmit={(e) => {
              e.preventDefault();
              resolve();
            }}
            className="flex flex-col gap-3"
          >
            <p className="text-sm leading-5">{KIND[look.kind]}</p>
            <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm leading-5">
              <dt className="text-fg-secondary">At Razorpay</dt>
              <dd className="m-0 text-right">
                <Amount paise={look.actual_paise} missing="Nothing at Razorpay" />
              </dd>
              <dt className="text-fg-secondary">In our ledger</dt>
              <dd className="m-0 text-right">
                <Amount paise={look.expected_paise} missing="Nothing in the ledger" />
              </dd>
              {look.actual_paise != null && look.expected_paise != null && (
                <>
                  <dt className="font-semibold">Difference</dt>
                  <dd className="m-0 text-right">
                    <MoneyText paise={look.actual_paise - look.expected_paise} size="sm" />
                  </dd>
                </>
              )}
            </dl>
            <p className="text-sm leading-5 text-fg-secondary">
              Check this reference in the Razorpay dashboard and the wallet’s journal entries first.
              Resolving does not credit or change anything: if the wallet needs a correction, make
              it as a manual adjustment entry.
            </p>
            <TextField
              label="What you decided"
              description="Required, up to 500 characters. Kept in the audit log."
              value={note}
              onChange={(v) => {
                setNote(v);
                setProblem("");
              }}
              maxLength={NOTE_MAX}
              isInvalid={!!problem}
              errorMessage={problem}
              className="max-w-none"
            />
            <Button type="submit" variant="primary" size="lg" block isDisabled={pending}>
              Resolve with note
            </Button>
          </form>
        )}
      </Sheet>
    </>
  );
}
