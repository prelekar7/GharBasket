"use client";

import { EmptyState, MoneyText } from "@gharbasket/ui";
import { PageHead } from "@/components/ops/PageHead.jsx";

const th = "border-b border-line-strong px-3 py-2.5 font-semibold whitespace-nowrap";

/**
 * Refunds waiting for review, read-only: refunds are paid automatically (M5 §3.3) and disputes are
 * handled as issues (M4 §3.6), so there is nothing to decide here.
 * @param {{ refunds: import("./page.jsx").Refund[] }} props
 */
export function RefundsView({ refunds }) {
  return (
    <>
      <PageHead title="Refunds">
        Short items and failed deliveries. Refunds go to the wallet automatically; a refund to the
        bank happens only when the customer asks. Disputes are handled as issues.
      </PageHead>

      {refunds.length === 0 ? (
        <EmptyState
          title="No refunds"
          body="Short items and failed deliveries show up here as they are refunded."
        />
      ) : (
        <div className="relative overflow-x-auto rounded-md border border-line">
          <table className="w-full min-w-[640px] border-collapse text-sm leading-5">
            <caption className="sr-only">Refunds</caption>
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
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
