"use client";

import { Button, Dialog, EmptyState, ProgressBar, toast } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { useRef, useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { StatTile } from "@/components/ops/StatTile.jsx";
import { clockLabel, dayLabel, istDay, timeLeft, weekdayName } from "@/lib/when.js";
import { rerunCutoff } from "./actions.js";

/** @type {import("@/components/ops/types.js").Columns} */
const COLUMNS = [
  { key: "flat", label: "Flat", sortable: true },
  { key: "society", label: "Society", sortable: true },
  { key: "box", label: "Box", align: "end", sortable: true },
  { key: "wallet", label: "Wallet", align: "end", sortable: true },
  { key: "short", label: "Short by", align: "end", sortable: true },
  { key: "what", label: "What happens" },
];

/**
 * The cut-off monitor: the night's numbers, the last run, and who is short in the wallet.
 * @param {{ hubId: string, cutoff: import("./page.jsx").Cutoff, canRun: boolean }} props
 */
export function CutoffView({ hubId, cutoff, canRun }) {
  const { counts, last_run: last, short } = cutoff;
  const [open, setOpen] = useState(false);
  const [pending, startTransition] = useTransition();
  // One Idempotency-Key per confirmation: a retried click replays, a new confirmation runs again.
  const key = useRef(crypto.randomUUID());
  const left = timeLeft(cutoff.as_of, cutoff.cutoff_at);
  const tonight = istDay(cutoff.cutoff_at) === istDay(cutoff.as_of);
  const grace = clockLabel(cutoff.grace_until);

  function confirm() {
    startTransition(async () => {
      const r = await rerunCutoff(hubId, key.current);
      key.current = crypto.randomUUID();
      toast(
        r.ok
          ? `Cut-off re-run: ${r.newlyLocked} new ${r.newlyLocked === 1 ? "order" : "orders"} locked, none charged twice.`
          : r.message,
      );
    });
  }

  /** @type {import("@/components/ops/types.js").Rows} */
  const rows = short.map((s) => ({
    id: s.order_id,
    flat: { text: s.flat, strong: true },
    society: s.society,
    box: { money: s.box_paise },
    wallet: { money: s.wallet_paise },
    short: { money: s.short_paise },
    what: `Asked to top up by ${grace}; otherwise the week is cancelled with no charge`,
  }));

  return (
    <>
      <PageHead
        title={`Cut-off · ${tonight ? "tonight, " : ""}${dayLabel(istDay(cutoff.cutoff_at))}, ${clockLabel(cutoff.cutoff_at)}`}
        action={
          canRun ? (
            <Button isDisabled={pending} onPress={() => setOpen(true)}>
              Re-run cut-off
            </Button>
          ) : (
            <span className="text-sm text-fg-secondary">Only an admin can re-run the cut-off.</span>
          )
        }
      >
        At {clockLabel(cutoff.cutoff_at)} approved boxes for {dayLabel(cutoff.delivery_date)} are
        paid from the wallet and locked for packing.
        {left === "now" ? " It is running now." : ` Starts in ${left}.`}
      </PageHead>

      <div className="grid grid-cols-2 gap-3 min-[900px]:grid-cols-4">
        <StatTile
          label="Approved"
          value={counts.approved}
          note={`${counts.auto_approved} by auto-approve`}
        />
        <StatTile label="Awaiting approval" value={counts.awaiting} note="Reminder sent 7 PM" />
        <StatTile label="Skipped" value={counts.skipped} note="No box, no charge" />
        <StatTile
          danger
          label="Payment needed"
          value={counts.payment_needed}
          note={`Grace until ${grace}`}
        />
      </div>

      {last && (
        <section className="flex flex-col gap-2 rounded-md border border-line p-4">
          <h2 className="text-[17px] leading-6 font-bold">Last run · {dayLabel(last.cutoff_on)}</h2>
          <ProgressBar
            label="Orders locked"
            value={last.locked}
            max={last.total}
            valueLabel={`${last.locked} of ${last.total} in ${last.duration_s} s`}
          />
          <p className="text-sm leading-5 text-fg-secondary">
            {last.grace_paid} {last.grace_paid === 1 ? "household" : "households"} paid during the{" "}
            {grace} grace;{" "}
            {last.cancelled === 0 ? "none were cancelled" : `${last.cancelled} were cancelled`}.
          </p>
        </section>
      )}

      <section className="flex flex-col gap-2">
        <h2 className="text-[17px] leading-6 font-bold">
          Wallet short for {weekdayName(cutoff.delivery_date)}’s box
        </h2>
        {short.length === 0 ? (
          <EmptyState
            title="No one is short"
            body="Every approved box can be paid from the wallet at the cut-off."
          />
        ) : (
          <DataTable
            caption="Households whose wallet is short for the box"
            columns={COLUMNS}
            rows={rows}
          />
        )}
      </section>

      <p className="text-sm leading-5 text-fg-secondary">
        A re-run is safe: it only locks orders not yet locked and never charges twice.
      </p>

      <Dialog
        title={`Re-run the cut-off for ${dayLabel(cutoff.delivery_date)}?`}
        confirmLabel="Re-run cut-off"
        cancelLabel="Cancel"
        isOpen={open}
        onOpenChange={setOpen}
        onConfirm={confirm}
      >
        Approved boxes are paid from the wallet and locked for packing. Orders already locked are
        skipped, so no one is charged twice.
      </Dialog>
    </>
  );
}
