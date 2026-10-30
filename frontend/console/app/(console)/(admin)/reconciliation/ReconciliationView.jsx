"use client";

import { Button, EmptyState, MoneyText, Sheet, TextField, toast } from "@gharbasket/ui";
import { useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { StatTile } from "@/components/ops/StatTile.jsx";
import { clockLabel, dayLabel, istDay, whenLabel } from "@/lib/when.js";
import { resolveMismatch } from "./actions.js";

/** The note's length in the contract (ResolveIn): 1 to 500 characters. */
const NOTE_MAX = 500;

const th = "border-b border-line-strong px-3 py-2.5 font-semibold whitespace-nowrap";

/**
 * Razorpay reconciliation: counts of what matched and a table of what didn't, with the fix for each.
 * @param {{ report: import("./page.jsx").Reconciliation }} props
 */
export function ReconciliationView({ report }) {
  const { counts, items } = report;
  const lastCheck = `${istDay(report.checked_at) === istDay(report.as_of) ? "today" : dayLabel(istDay(report.checked_at))}, ${clockLabel(report.checked_at)}`;
  const [pending, startTransition] = useTransition();
  // The item being resolved, and the note for it. Not saved until "Resolve with note".
  const [look, setLook] = useState(/** @type {import("./page.jsx").Mismatch | null} */ (null));
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState("");

  /** @param {import("./page.jsx").Mismatch | null} item */
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
        toast(`${item.provider_ref} is resolved.`);
        open(null);
      } else setProblem(r.fields.note ?? r.message);
    });
  }

  return (
    <>
      <PageHead title="Reconciliation with Razorpay">
        Every hour, each Razorpay payment is matched to a wallet entry in our ledger. Last check{" "}
        {lastCheck}, for the last {report.window_hours} hours.
      </PageHead>

      <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-4">
        <StatTile label="Matched" value={counts.matched} />
        <StatTile
          danger={counts.no_wallet_entry > 0}
          label="Paid, no wallet entry"
          value={counts.no_wallet_entry}
        />
        <StatTile
          danger={counts.amount_differs > 0}
          label="Amount differs"
          value={counts.amount_differs}
        />
        <StatTile
          danger={counts.no_payment > 0}
          label="Wallet entry, no payment"
          value={counts.no_payment}
        />
      </div>

      {items.length === 0 ? (
        <EmptyState
          title="Everything matches"
          body="Every Razorpay payment from the last 24 hours has its wallet entry."
        />
      ) : (
        <div className="relative overflow-x-auto rounded-md border border-line">
          <table className="w-full min-w-[820px] border-collapse text-sm leading-5">
            <caption className="sr-only">Payments that don’t match the ledger</caption>
            <thead>
              <tr className="text-left text-fg-secondary">
                <th scope="col" className={th}>
                  Razorpay payment
                </th>
                <th scope="col" className={th}>
                  When
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
                    <code>{m.provider_ref}</code>
                  </td>
                  <td className="px-3 py-2.5 whitespace-nowrap">{whenLabel(m.at, report.as_of)}</td>
                  <td className="px-3 py-2.5 text-right">
                    <MoneyText paise={m.razorpay_paise} size="sm" />
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    {m.ledger_paise == null ? (
                      <>
                        <span aria-hidden>—</span>
                        <span className="sr-only">Nothing in the ledger</span>
                      </>
                    ) : (
                      <MoneyText paise={m.ledger_paise} size="sm" />
                    )}
                  </td>
                  <td className="px-3 py-2.5">{m.note}</td>
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
        description={look ? `${look.provider_ref} · ${look.payer}` : undefined}
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
            <p className="text-sm leading-5">{look.note}</p>
            <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm leading-5">
              <dt className="text-fg-secondary">At Razorpay</dt>
              <dd className="m-0 text-right">
                <MoneyText paise={look.razorpay_paise} size="sm" />
              </dd>
              <dt className="text-fg-secondary">In our ledger</dt>
              <dd className="m-0 text-right">
                <MoneyText paise={look.ledger_paise ?? 0} size="sm" />
              </dd>
              <dt className="font-semibold">Difference</dt>
              <dd className="m-0 text-right">
                <MoneyText paise={look.razorpay_paise - (look.ledger_paise ?? 0)} size="sm" />
              </dd>
            </dl>
            <p className="text-sm leading-5 text-fg-secondary">
              Check this payment in the Razorpay dashboard and the wallet’s journal entries first.
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
