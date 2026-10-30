"use client";

import { EmptyState, MoneyText } from "@gharbasket/ui";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { formatDay } from "@/lib/fleet.js";
import { kindText, needsAttention, reasonText, statusText } from "@/lib/refunds.js";

const th = "border-b border-line-strong px-3 py-2.5 font-semibold whitespace-nowrap";

/**
 * What went back for orders, read-only: refunds are paid automatically (M5 §3.3) and disputes are
 * handled as issues (M4 §3.6), so there is nothing to decide here.
 * @param {{ refunds: import("./page.jsx").RefundRow[], truncated?: boolean }} props
 */
export function RefundsView({ refunds, truncated = false }) {
  return (
    <>
      <PageHead title="Refunds">
        What went back for orders in the last 7 days, by delivery date. Refunds go to the wallet at
        once; one goes to the bank only when the wallet is full. Disputes are handled as issues.
      </PageHead>

      {refunds.length === 0 ? (
        <EmptyState
          title="No refunds"
          body="Short items and failed deliveries show up here as they are refunded."
        />
      ) : (
        <div className="relative overflow-x-auto rounded-md border border-line">
          <table className="w-full min-w-[720px] border-collapse text-sm leading-5">
            <caption className="sr-only">Refunds</caption>
            <thead>
              <tr className="text-left text-fg-secondary">
                <th scope="col" className={th}>
                  Delivery
                </th>
                <th scope="col" className={th}>
                  Flat · society
                </th>
                <th scope="col" className={th}>
                  Why
                </th>
                <th scope="col" className={`${th} text-right`}>
                  Refund
                </th>
                <th scope="col" className={th}>
                  Where it went
                </th>
              </tr>
            </thead>
            <tbody>
              {refunds.map((r) => (
                <tr key={r.id} className="border-b border-line align-middle last:border-b-0">
                  <td className="px-3 py-2.5">
                    <strong>{formatDay(r.delivery_date)}</strong>
                    <div className="text-[13px] leading-[18px] text-fg-secondary">
                      {kindText(r.order_kind)}
                    </div>
                  </td>
                  <td className="px-3 py-2.5">
                    <strong>{r.flat_label ?? "No flat on the order"}</strong>
                    <div className="text-[13px] leading-[18px] text-fg-secondary">
                      {r.society.name}
                    </div>
                  </td>
                  <td className="px-3 py-2.5">{reasonText(r.reason)}</td>
                  <td className="px-3 py-2.5 text-right font-bold">
                    <MoneyText paise={r.amount_paise} size="sm" />
                  </td>
                  <td className="px-3 py-2.5">
                    <strong>{r.to === "wallet" ? "Wallet" : "Bank"}</strong>
                    <div
                      className={`text-[13px] leading-[18px] ${needsAttention(r.status) ? "text-danger" : "text-fg-secondary"}`}
                    >
                      {statusText(r.status)}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {truncated && (
        <p role="status" className="text-sm leading-5 text-fg-secondary">
          Showing the newest {refunds.length} refunds; older ones in this window are not listed.
        </p>
      )}
    </>
  );
}
