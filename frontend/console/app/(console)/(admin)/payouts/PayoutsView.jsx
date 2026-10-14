"use client";

import { Badge, Button, Dialog, formatPaise, MoneyText, Select, toast } from "@gharbasket/ui";
import { DataTable, KpiTile } from "@gharbasket/ui/console";
import { parseAsString, useQueryState } from "nuqs";
import { useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { KIND_LABEL } from "@/lib/partners.js";
import { runAfter, weekRange } from "@/lib/payout-weeks.js";
import { clockLabel, dateLabel, dayLabel, istDay } from "@/lib/when.js";
import { retryPayout } from "./actions.js";

/** @type {import("@/components/ops/types.js").Columns} */
const COLUMNS = [
  { key: "who", label: "Partner", sortable: true },
  { key: "role", label: "Role" },
  { key: "amount", label: "Amount", align: "end", sortable: true },
  { key: "kyc", label: "KYC" },
  { key: "state", label: "Payout" },
];

const HOLD = /** @type {Record<string, string>} */ ({
  kyc_incomplete: "KYC incomplete",
  suspended: "Suspended",
  no_payout_account: "No payout account",
});

/** @type {Record<import("@/mocks/payouts.js").Payout["status"], [("primary" | "outline" | "danger"), string]>} */
const STATE = {
  processed: ["primary", "Paid"],
  processing: ["outline", "Sending"],
  pending: ["outline", "Queued"],
  held: ["outline", "Held"],
  failed: ["danger", "Failed"],
};

/**
 * Weekly partner payouts: the totals, one row per partner, and what to do about a failed payout.
 * @param {{ week: { week: string, from: string, to: string }, weeks: { week: string, from: string, to: string }[],
 *   items: import("@/mocks/payouts.js").Payout[], canRetry: boolean }} props
 */
export function PayoutsView({ week, weeks, items, canRetry }) {
  const [pending, startTransition] = useTransition();
  const [, setWeek] = useQueryState(
    "week",
    parseAsString.withOptions({ shallow: false, startTransition }),
  );
  const [asking, setAsking] = useState(
    /** @type {{ payout: import("@/mocks/payouts.js").Payout, key: string } | null} */ (null),
  );
  const failed = items.filter((p) => p.status === "failed");
  const sum = (/** @type {import("@/mocks/payouts.js").Payout["status"]} */ s) =>
    items.filter((p) => p.status === s).reduce((t, p) => t + p.amount_paise, 0);
  const count = (/** @type {import("@/mocks/payouts.js").Payout["status"]} */ s) =>
    items.filter((p) => p.status === s).length;
  const paidOn = items
    .map((p) => p.processed_at)
    .filter((t) => t !== null)
    .sort()
    .at(-1);
  const nextRun = runAfter({
    to: new Date(Date.parse(`${weeks[0].to}T00:00:00Z`) + 7 * 86_400_000)
      .toISOString()
      .slice(0, 10),
  });
  const heldFirst = items.find((p) => p.status === "held");
  const heldDetail = heldFirst?.hold_reason ? HOLD[heldFirst.hold_reason] : "Waiting for a fix";

  /** @type {import("@/components/ops/types.js").Rows} */
  const rows = items.map((p) => {
    const [tone, label] = STATE[p.status];
    return {
      id: p.id,
      who: { text: p.partner.display_name, sub: p.provider_ref ?? undefined, strong: true },
      role: KIND_LABEL[p.partner.kind],
      amount: { money: p.amount_paise },
      kyc: {
        badge: {
          tone: p.hold_reason === "kyc_incomplete" ? "outline" : "primary",
          label: p.hold_reason === "kyc_incomplete" ? "Pending" : "Verified",
        },
      },
      state: {
        badge: {
          tone,
          label: p.status === "held" && p.hold_reason ? `Held · ${HOLD[p.hold_reason]}` : label,
        },
      },
      __tone: p.status === "failed" ? "danger" : undefined,
    };
  });

  /** @param {import("@/mocks/payouts.js").Payout} p @param {string} key */
  function retry(p, key) {
    startTransition(async () => {
      const r = await retryPayout(p.id, key);
      toast(
        r.ok ? `Retrying ${formatPaise(p.amount_paise)} to ${p.partner.display_name}.` : r.message,
      );
    });
  }

  return (
    <>
      <PageHead
        title={`Partner payouts · week of ${dateLabel(week.from)}`}
        action={
          items.some((p) => p.provider === "simulated") && (
            <Badge tone="accent">Test mode · no real money moves</Badge>
          )
        }
      >
        The Monday {clockLabel(nextRun)} run pays partners whose KYC is accepted and who have a
        payout account, from the ledger. Anyone else is held, not skipped, and paid once that is
        fixed.
      </PageHead>

      <Select
        label="Week"
        value={week.week}
        onChange={(v) => setWeek(v === weeks[0].week ? null : v)}
        options={weeks.map((w) => ({ value: w.week, label: `${weekRange(w)} (${w.week})` }))}
      />

      <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-4">
        <KpiTile
          label={paidOn ? `Paid on ${dayLabel(istDay(paidOn))}` : "Paid"}
          value={formatPaise(sum("processed"))}
          hint={`${count("processed")} partners`}
        />
        <KpiTile
          label="Held"
          value={formatPaise(sum("held"))}
          hint={count("held") === 0 ? "Every partner was eligible this week" : heldDetail}
        />
        <KpiTile
          label="Failed"
          value={String(count("failed"))}
          hint={
            count("failed") > 0
              ? `${formatPaise(sum("failed"))} · needs a fix first`
              : "Nothing to fix"
          }
        />
        <KpiTile label="Next run" value={dayLabel(istDay(nextRun))} hint={clockLabel(nextRun)} />
      </div>

      <div
        className={`grid grid-cols-1 items-start gap-4 ${failed.length > 0 ? "min-[1100px]:grid-cols-[minmax(0,1.8fr)_minmax(0,1fr)]" : ""}`}
      >
        <div className="min-w-0">
          <DataTable
            caption={`Payouts for ${weekRange(week)}`}
            columns={COLUMNS}
            rows={rows}
            footer="Each amount is the sum of that partner’s ledger earnings for the week."
            labels={{ empty: "No payouts for this week yet." }}
          />
        </div>
        {failed.map((p) => (
          <section
            key={p.id}
            aria-label="Failed payout"
            className="flex min-w-0 flex-col gap-3 rounded-md bg-surface p-4"
          >
            <h2 className="text-[17px] leading-6 font-bold">
              {p.partner.display_name} · payout failed
            </h2>
            <p className="text-sm leading-5 text-fg-secondary">
              The transfer did not go through. Nothing was lost; the money stays in their earnings
              until it’s paid.
            </p>
            <MoneyText paise={p.amount_paise} size="lg" />
            <p className="text-sm leading-5 text-fg-secondary">
              Ask {p.partner.display_name.split(" ")[0]} to check their bank details in the app,
              then retry.
            </p>
            {canRetry ? (
              <Button
                variant="primary"
                block
                isDisabled={pending}
                onPress={() => setAsking({ payout: p, key: crypto.randomUUID() })}
              >
                Retry {formatPaise(p.amount_paise)} now
              </Button>
            ) : (
              <p className="text-sm leading-5 text-fg-secondary">
                Only an admin can retry a payout.
              </p>
            )}
          </section>
        ))}
      </div>

      <Dialog
        title={
          asking
            ? `Retry ${formatPaise(asking.payout.amount_paise)} to ${asking.payout.partner.display_name}?`
            : ""
        }
        confirmLabel={asking ? `Pay ${formatPaise(asking.payout.amount_paise)}` : ""}
        cancelLabel="Cancel"
        isOpen={asking !== null}
        onOpenChange={(o) => !o && setAsking(null)}
        onConfirm={() => asking && retry(asking.payout, asking.key)}
      >
        {asking &&
          `This sends ${formatPaise(asking.payout.amount_paise)} from their ledger earnings to their bank account. Check that ${asking.payout.partner.display_name.split(" ")[0]} has fixed the bank details first.`}
      </Dialog>
    </>
  );
}
