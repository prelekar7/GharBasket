"use client";

import { Badge, EmptyState, formatPaise } from "@gharbasket/ui";
import { featureLabel, PLAN_KINDS } from "@/lib/plans.js";

/** @typedef {import("@/lib/plans.js").Plan} Plan */

/**
 * Plans (M3): what a household can subscribe to. One card per plan, read-only: plans are seeded and
 * a fee is a price, so the team changes them in the database, not here (M3 §3.5).
 * @param {{ plans: Plan[] }} props
 */
export function PlansView({ plans }) {
  return (
    <>
      <div className="flex max-w-3xl flex-col gap-0.5">
        <h1 className="font-display text-[28px] leading-[34px] font-semibold">Plans</h1>
        <p className="text-sm leading-5 text-fg-secondary">
          What a household can subscribe to: the weekly Autopilot box, tiffins and, later, salads.
          Each plan has a code, a monthly fee and its features.
        </p>
      </div>

      {plans.length === 0 ? (
        <div className="rounded-md border border-line">
          <EmptyState title="No plans" body="No plans have been set up yet." />
        </div>
      ) : (
        <div className="grid items-start gap-4 min-[700px]:grid-cols-2 min-[1100px]:grid-cols-3">
          {plans.map((p) => (
            <section
              key={p.code}
              aria-label={p.name}
              className="flex min-w-0 flex-col gap-2.5 rounded-md bg-surface p-[18px]"
            >
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-[17px] leading-6 font-bold">{p.name}</h2>
                <Badge tone={p.state === "live" ? "primary" : "outline"}>
                  {p.state === "live" ? "Live" : "Draft"}
                </Badge>
              </div>
              <span className="text-sm leading-5 text-fg-secondary">
                {p.code} · {PLAN_KINDS[p.code]}
              </span>
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm leading-5 text-fg-secondary">Monthly fee</span>
                <strong>{p.fee_paise === 0 ? "Not set" : formatPaise(p.fee_paise)}</strong>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm leading-5 text-fg-secondary">Subscribers</span>
                <strong>{p.subscriber_count}</strong>
              </div>
              <ul className="m-0 list-disc pl-[18px] text-sm leading-[22px]">
                {p.features.map((f) => (
                  <li key={f}>{featureLabel(f)}</li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
      <p className="text-sm leading-5 text-fg-secondary">
        A fee of “Not set” means pricing is not decided. A draft plan is hidden from households.
        Subscribers are the subscriptions on the plan that have not ended.
      </p>
    </>
  );
}
