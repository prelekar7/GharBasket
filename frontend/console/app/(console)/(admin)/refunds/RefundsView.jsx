"use client";

import { Button, Dialog, EmptyState, formatPaise, MoneyText, toast } from "@gharbasket/ui";
import { useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { decideRefund } from "./actions.js";

const th = "border-b border-line-strong px-3 py-2.5 font-semibold whitespace-nowrap";

/**
 * Refunds waiting for a decision. Approving pays the wallet at once, so it confirms with a Dialog;
 * rejecting moves no money and has an Undo toast instead.
 * @param {{ refunds: import("./page.jsx").Refund[] }} props
 */
export function RefundsView({ refunds }) {
  const [pending, startTransition] = useTransition();
  // The refund being confirmed, with one Idempotency-Key per confirmation.
  const [asking, setAsking] = useState(
    /** @type {{ refund: import("./page.jsx").Refund, key: string } | null} */ (null),
  );

  /** @param {import("./page.jsx").Refund} r */
  function approve(r, key = crypto.randomUUID()) {
    startTransition(async () => {
      const res = await decideRefund(r.id, "approve", key);
      toast(
        res.ok ? `Refunded ${formatPaise(r.amount_paise)} to ${r.flat}’s wallet.` : res.message,
      );
    });
  }

  /** @param {import("./page.jsx").Refund} r */
  function reject(r) {
    startTransition(async () => {
      const res = await decideRefund(r.id, "reject", crypto.randomUUID());
      if (!res.ok) return void toast(res.message);
      toast(`Rejected the refund for ${r.flat}.`, {
        action: {
          label: "Undo",
          onClick: async () => {
            const back = await decideRefund(r.id, "reopen", crypto.randomUUID());
            toast(back.ok ? "Put back in the list." : back.message);
          },
        },
      });
    });
  }

  return (
    <>
      <PageHead title="Refunds to review">
        Short items and failed deliveries. Refunds go to the wallet at once; a refund to the bank
        happens only when the customer asks.
      </PageHead>

      {refunds.length === 0 ? (
        <EmptyState
          title="Nothing to review"
          body="Short items and failed deliveries show up here as they are reported."
        />
      ) : (
        <div className="relative overflow-x-auto rounded-md border border-line">
          <table className="w-full min-w-[820px] border-collapse text-sm leading-5">
            <caption className="sr-only">Refunds waiting for a decision</caption>
            <thead>
              <tr className="text-left text-fg-secondary">
                <th scope="col" className={th}>
                  Order
                </th>
                <th scope="col" className={th}>
                  Why
                </th>
                <th scope="col" className={th}>
                  Reported by
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Refund
                </th>
                <th scope="col" className={th}>
                  To
                </th>
                <th scope="col" className={th}>
                  <span className="sr-only">Decision</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {refunds.map((r) => (
                <tr key={r.id} className="border-b border-line align-middle last:border-b-0">
                  <td className="px-3 py-2.5">
                    <strong>
                      {r.flat} · {r.society}
                    </strong>
                    <div className="text-[13px] leading-[18px] text-fg-secondary">
                      {r.order_label}
                    </div>
                  </td>
                  <td className="px-3 py-2.5">{r.why}</td>
                  <td className="px-3 py-2.5 text-fg-secondary">{r.reported_by}</td>
                  <td className="px-3 py-2.5 text-right font-bold">
                    <MoneyText paise={r.amount_paise} size="sm" />
                  </td>
                  <td className="px-3 py-2.5">Wallet</td>
                  <td className="px-3 py-2.5">
                    <div className="flex justify-end gap-1.5">
                      <Button variant="quiet" isDisabled={pending} onPress={() => reject(r)}>
                        Reject<span className="sr-only"> refund for {r.flat}</span>
                      </Button>
                      <Button
                        variant="primary"
                        isDisabled={pending}
                        onPress={() => setAsking({ refund: r, key: crypto.randomUUID() })}
                      >
                        Approve<span className="sr-only"> refund for {r.flat}</span>
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Dialog
        title={asking ? `Refund ${formatPaise(asking.refund.amount_paise)} to the wallet?` : ""}
        confirmLabel={asking ? `Refund ${formatPaise(asking.refund.amount_paise)}` : ""}
        cancelLabel="Cancel"
        isOpen={asking !== null}
        onOpenChange={(o) => !o && setAsking(null)}
        onConfirm={() => asking && approve(asking.refund, asking.key)}
      >
        {asking &&
          `${asking.refund.flat}, ${asking.refund.society}, ${asking.refund.what_happened}. The wallet goes from ${formatPaise(asking.refund.wallet_before_paise)} to ${formatPaise(asking.refund.wallet_before_paise + asking.refund.amount_paise)}, and they get a notification.`}
      </Dialog>
    </>
  );
}
