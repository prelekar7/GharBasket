"use client";

import {
  Badge,
  Button,
  Dialog,
  EmptyState,
  formatPaise,
  MoneyText,
  Sheet,
  toast,
} from "@gharbasket/ui";
import { useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { StatTile } from "@/components/ops/StatTile.jsx";
import { clockLabel, dayLabel, istDay, whenLabel } from "@/lib/when.js";
import { checkNow, creditWallet, markInvestigating } from "./actions.js";

const th = "border-b border-line-strong px-3 py-2.5 font-semibold whitespace-nowrap";

/**
 * Razorpay reconciliation: counts of what matched and a table of what didn't, with the fix for each.
 * @param {{ report: import("./page.jsx").Reconciliation }} props
 */
export function ReconciliationView({ report }) {
  const { counts, items } = report;
  const lastCheck = `${istDay(report.checked_at) === istDay(report.as_of) ? "today" : dayLabel(istDay(report.checked_at))}, ${clockLabel(report.checked_at)}`;
  const [pending, startTransition] = useTransition();
  // The mismatch being confirmed or looked at, with one Idempotency-Key per confirmation.
  const [credit, setCredit] = useState(
    /** @type {{ item: import("./page.jsx").Mismatch, key: string } | null} */ (null),
  );
  const [look, setLook] = useState(/** @type {import("./page.jsx").Mismatch | null} */ (null));

  /** @param {() => Promise<{ ok: true } | { ok: false, message: string }>} run @param {string} done */
  function run(run, done) {
    startTransition(async () => {
      const r = await run();
      toast(r.ok ? done : r.message);
    });
  }

  return (
    <>
      <PageHead
        title="Reconciliation with Razorpay"
        action={
          <Button
            isDisabled={pending}
            onPress={() => run(() => checkNow(crypto.randomUUID()), "Checked just now.")}
          >
            Check now
          </Button>
        }
      >
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
                  <td className="px-3 py-2.5">
                    {m.note} {m.investigating && <Badge tone="outline">Investigating</Badge>}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    {m.kind === "no_wallet_entry" ? (
                      <Button
                        variant="primary"
                        isDisabled={pending}
                        onPress={() => setCredit({ item: m, key: crypto.randomUUID() })}
                      >
                        Credit wallet
                      </Button>
                    ) : (
                      <Button onPress={() => setLook(m)}>Investigate</Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-sm leading-5 text-fg-secondary">
        Fixes post a correcting ledger entry; nothing is edited or deleted. Each fix is saved to the
        audit log with your name.
      </p>

      <Dialog
        title={credit ? `Credit ${formatPaise(credit.item.razorpay_paise)} to the wallet?` : ""}
        confirmLabel={credit ? `Credit ${formatPaise(credit.item.razorpay_paise)}` : ""}
        cancelLabel="Cancel"
        isOpen={credit !== null}
        onOpenChange={(o) => !o && setCredit(null)}
        onConfirm={() => {
          if (!credit) return;
          const { item, key } = credit;
          run(
            () => creditWallet(item.id, key),
            `Credited ${formatPaise(item.razorpay_paise)} to ${item.payer}.`,
          );
        }}
      >
        {credit &&
          `${credit.item.provider_ref} (${credit.item.payer}) was captured at Razorpay but never reached the wallet. A correcting ledger entry credits it, and they get a notification. It is saved to the audit log with your name.`}
      </Dialog>

      <Sheet
        title="Amounts differ"
        description={look ? `${look.provider_ref} · ${look.payer}` : undefined}
        open={look !== null}
        onOpenChange={(o) => !o && setLook(null)}
      >
        {look && (
          <div className="flex flex-col gap-3">
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
              Check this payment in the Razorpay dashboard and the wallet’s journal entries. A
              mismatch is only fixed by a correcting entry, never by editing the old one.
            </p>
            <Button
              variant="primary"
              size="lg"
              block
              isDisabled={pending || look.investigating}
              onPress={() => {
                const id = look.id;
                setLook(null);
                run(
                  () => markInvestigating(id, crypto.randomUUID()),
                  "Marked as being investigated.",
                );
              }}
            >
              {look.investigating ? "Already being investigated" : "Mark as investigating"}
            </Button>
          </div>
        )}
      </Sheet>
    </>
  );
}
