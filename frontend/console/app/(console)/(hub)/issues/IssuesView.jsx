"use client";

import { Badge, EmptyState, formatPaise, MoneyText, Tabs } from "@gharbasket/ui";
import { parseAsStringLiteral, useQueryState } from "nuqs";
import { useState, useTransition } from "react";
import { PageHead } from "@/components/ops/PageHead.jsx";
import { KIND_LABEL, OPEN_REASON } from "@/lib/order-issues.js";
import { dayLabel, dayTimeLabel } from "@/lib/when.js";
import { IssueSheet } from "./IssueSheet.jsx";

const th = "border-b border-line-strong px-3 py-2.5 font-semibold whitespace-nowrap";
const STATUSES = /** @type {const} */ (["open", "credited", "rejected"]);

/**
 * One row of GET /v1/admin/order-issues (contract `IssueTriage`), the fields the queue shows.
 * @typedef {{ id: string, kind: string, status: "open" | "credited" | "rejected", delivery_date: string, created_at: string,
 *   amount_paise: number, credit_paise: number, open_reason: string | null, callback_due_at: string | null,
 *   items: { name: string, qty: number }[] }} IssueRow
 */

/**
 * The hub's issue queue, oldest first: the customer who waited longest is at the top. Pick a row to
 * see its photos and contact, then credit it to the wallet or reject it.
 * @param {{ status: IssueRow["status"], issues: IssueRow[], more: boolean, hubName: string }} props
 */
export function IssuesView({ status, issues, more, hubName }) {
  const [, startTransition] = useTransition();
  const [, setStatus] = useQueryState(
    "status",
    parseAsStringLiteral(STATUSES)
      .withDefault("open")
      .withOptions({ shallow: false, startTransition }),
  );
  const [pickedId, setPickedId] = useState(/** @type {string | null} */ (null));

  return (
    <>
      <PageHead title="Issues">
        What customers reported about {hubName}'s deliveries, oldest first. Open issues need a
        decision and a call back.
      </PageHead>
      <Tabs
        label="Issues by state"
        tabs={[
          { value: "open", label: "Open" },
          { value: "credited", label: "Credited" },
          { value: "rejected", label: "Rejected" },
        ]}
        value={status}
        onChange={(v) => void setStatus(/** @type {IssueRow["status"]} */ (v))}
      >
        {issues.length === 0 ? (
          <EmptyState
            title={status === "open" ? "Nothing waiting" : "Nothing here yet"}
            body={
              status === "open"
                ? "Issues that need a person to decide show up here as customers report them."
                : "Issues you resolve are listed here."
            }
          />
        ) : (
          <div className="relative overflow-x-auto rounded-md border border-line">
            <table className="w-full min-w-[760px] border-collapse text-sm leading-5">
              <caption className="sr-only">Issues reported by customers, oldest first</caption>
              <thead>
                <tr className="text-left text-fg-secondary">
                  <th scope="col" className={th}>
                    Reported
                  </th>
                  <th scope="col" className={th}>
                    Issue
                  </th>
                  <th scope="col" className={th}>
                    Delivery
                  </th>
                  <th scope="col" className={`${th} text-right`}>
                    {status === "credited" ? "Credited" : "Claimed"}
                  </th>
                  <th scope="col" className={th}>
                    {status === "open" ? "Call back by" : "State"}
                  </th>
                </tr>
              </thead>
              <tbody>
                {issues.map((i) => (
                  <tr key={i.id} className="border-b border-line align-middle last:border-b-0">
                    <td className="px-3 py-2.5 whitespace-nowrap text-fg-secondary">
                      {dayTimeLabel(i.created_at)}
                    </td>
                    <td className="px-3 py-1.5">
                      <button
                        type="button"
                        onClick={() => setPickedId(i.id)}
                        className="flex min-h-11 cursor-default flex-col rounded-xs text-left outline-none focus-visible:shadow-focus"
                      >
                        <span className="font-semibold">{KIND_LABEL[i.kind] ?? i.kind}</span>
                        <span className="text-[13px] leading-[18px] text-fg-secondary">
                          {i.items.map((x) => `${x.qty} × ${x.name}`).join(", ") || "Whole order"}
                        </span>
                      </button>
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap">{dayLabel(i.delivery_date)}</td>
                    <td className="px-3 py-2.5 text-right font-bold">
                      <MoneyText
                        paise={status === "credited" ? i.credit_paise : i.amount_paise}
                        size="sm"
                      />
                    </td>
                    <td className="px-3 py-2.5">
                      {status === "open" ? (
                        <span title={i.open_reason ? OPEN_REASON[i.open_reason] : undefined}>
                          {i.callback_due_at ? dayTimeLabel(i.callback_due_at) : "-"}
                          {i.open_reason && (
                            <span className="block text-[13px] leading-[18px] text-fg-secondary">
                              {OPEN_REASON[i.open_reason] ?? i.open_reason}
                            </span>
                          )}
                        </span>
                      ) : (
                        <Badge tone={status === "credited" ? "primary" : "outline"}>
                          {status === "credited"
                            ? `Credited ${formatPaise(i.credit_paise)}`
                            : "Rejected"}
                        </Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {more && (
          <p className="text-sm leading-5 text-fg-secondary">
            Showing the oldest 50. Resolve some to see the rest.
          </p>
        )}
      </Tabs>
      <IssueSheet issueId={pickedId} onClose={() => setPickedId(null)} />
    </>
  );
}
