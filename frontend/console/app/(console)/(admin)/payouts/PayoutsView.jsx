"use client";

import { Badge, Button, Dialog, formatPaise, MoneyText, toast } from "@gharbasket/ui";
import { DataTable, KpiTile } from "@gharbasket/ui/console";
import { useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { clockLabel, dateLabel, dayLabel, istDay } from "@/lib/when.js";
import { retryPayout } from "./actions.js";

/** @type {import("@/components/ops/types.js").Columns} */
const COLUMNS = [
  { key: "who", label: "Partner", sortable: true },
  { key: "role", label: "Role" },
  { key: "work", label: "Work" },
  { key: "amount", label: "Amount", align: "end", sortable: true },
  { key: "kyc", label: "KYC" },
  { key: "state", label: "Payout" },
];

/**
 * Weekly partner payouts: the totals, one row per partner, and what to do about a failed payout.
 * @param {{ payouts: import("./page.jsx").Payouts }} props
 */
export function PayoutsView({ payouts }) {
  const { totals, items } = payouts;
  const [pending, startTransition] = useTransition();
  const [asking, setAsking] = useState(
    /** @type {{ payout: import("./page.jsx").Payout, key: string } | null} */ (null),
  );
  const failed = items.filter((p) => p.status === "failed");

  /** @type {import("@/components/ops/types.js").Rows} */
  const rows = items.map((p) => ({
    id: p.id,
    who: { text: p.name, sub: p.masked_account, strong: true },
    role: p.role,
    work: p.work,
    amount: { money: p.amount_paise },
    kyc: {
      badge: {
        tone: p.kyc === "verified" ? "primary" : "outline",
        label: p.kyc === "verified" ? "Verified" : "Pending",
      },
    },
    state: {
      badge: {
        tone: p.status === "paid" ? "primary" : p.status === "failed" ? "danger" : "outline",
        label:
          p.status === "paid"
            ? "Paid"
            : p.status === "failed"
              ? `Failed${p.failure ? ` · ${p.failure}` : ""}`
              : "Held",
      },
    },
    __tone: p.status === "failed" ? "danger" : undefined,
  }));

  /** @param {import("./page.jsx").Payout} p @param {string} key */
  function retry(p, key) {
    startTransition(async () => {
      const r = await retryPayout(p.id, key);
      toast(r.ok ? `Paid ${formatPaise(p.amount_paise)} to ${p.name}.` : r.message);
    });
  }

  return (
    <>
      <PageHead
        title={`Partner payouts · week of ${dateLabel(payouts.week_start)}`}
        action={payouts.test_mode && <Badge tone="accent">Test mode · no real money moves</Badge>}
      >
        The Monday {clockLabel(payouts.next_run)} run pays verified partners only, from the ledger.
        Anyone not verified is held, not skipped, and paid once their KYC clears.
      </PageHead>

      <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-4">
        <KpiTile
          label={`Paid on ${dayLabel(payouts.paid_on)}`}
          value={formatPaise(totals.paid_paise)}
          hint={`${totals.paid_partners} partners`}
        />
        <KpiTile
          label="Held"
          value={formatPaise(totals.held_paise)}
          hint={
            totals.held_paise === 0 ? "No unverified partner earned this week" : "Waiting for KYC"
          }
        />
        <KpiTile
          label="Failed"
          value={String(totals.failed_count)}
          hint={
            totals.failed_count > 0
              ? `${formatPaise(totals.failed_paise)} · needs a fix first`
              : "Nothing to fix"
          }
        />
        <KpiTile
          label="Next run"
          value={dayLabel(istDay(payouts.next_run))}
          hint={clockLabel(payouts.next_run)}
        />
      </div>

      <div
        className={`grid grid-cols-1 items-start gap-4 ${failed.length > 0 ? "min-[1100px]:grid-cols-[minmax(0,1.8fr)_minmax(0,1fr)]" : ""}`}
      >
        <div className="min-w-0">
          <DataTable
            caption={`Payouts for the week of ${dateLabel(payouts.week_start)}`}
            columns={COLUMNS}
            rows={rows}
            footer="Each amount is the sum of that partner’s ledger earnings for the week."
          />
        </div>
        {failed.map((p) => (
          <section
            key={p.id}
            aria-label="Failed payout"
            className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
          >
            <h2 className="text-[17px] leading-6 font-bold">{p.name} · payout failed</h2>
            <p className="text-sm leading-5 text-fg-secondary">
              The bank returned it
              {p.failure === "name mismatch" ? ": the account name didn’t match" : ""}. Nothing was
              lost; the money stays in their earnings until it’s paid.
            </p>
            <MoneyText paise={p.amount_paise} size="lg" />
            <p className="text-sm leading-5 text-fg-secondary">
              Ask {p.name.split(" ")[0]} to check their bank details in the app, then retry.
            </p>
            <Button
              variant="primary"
              block
              isDisabled={pending}
              onPress={() => setAsking({ payout: p, key: crypto.randomUUID() })}
            >
              Retry {formatPaise(p.amount_paise)} now
            </Button>
          </section>
        ))}
      </div>

      <Dialog
        title={
          asking ? `Retry ${formatPaise(asking.payout.amount_paise)} to ${asking.payout.name}?` : ""
        }
        confirmLabel={asking ? `Pay ${formatPaise(asking.payout.amount_paise)}` : ""}
        cancelLabel="Cancel"
        isOpen={asking !== null}
        onOpenChange={(o) => !o && setAsking(null)}
        onConfirm={() => asking && retry(asking.payout, asking.key)}
      >
        {asking &&
          `This sends ${formatPaise(asking.payout.amount_paise)} from their ledger earnings to the account ending ${asking.payout.masked_account.slice(-4)}. Check that ${asking.payout.name.split(" ")[0]} has fixed the bank details first.`}
      </Dialog>
    </>
  );
}
