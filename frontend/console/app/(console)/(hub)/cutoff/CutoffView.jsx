"use client";

import { EmptyState, ProgressBar } from "@gharbasket/ui";
import { DataTable } from "@gharbasket/ui/console";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { StatTile } from "@/components/ops/StatTile.jsx";
import { clockLabel, dayLabel, istDay, timeLeft, weekdayName } from "@/lib/when.js";

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
 * The cut-off monitor: the night's numbers, the last run, and who is short in the wallet. Read only:
 * the `cutoff_lock` job runs the cut-off at 21:00, nobody runs it by hand.
 * `hubId` and `canRun` are what page.jsx used to pass for the removed "Re-run cut-off" button; they are
 * ignored, and page.jsx stops passing them once its other branch lands.
 * @param {{ cutoff: import("./page.jsx").Cutoff, hubId?: string, canRun?: boolean }} props
 */
export function CutoffView({ cutoff }) {
  const { counts, last_run: last, short } = cutoff;
  const left = timeLeft(cutoff.as_of, cutoff.cutoff_at);
  const tonight = istDay(cutoff.cutoff_at) === istDay(cutoff.as_of);
  const grace = clockLabel(cutoff.grace_until);

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
    </>
  );
}
