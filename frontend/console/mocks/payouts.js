// Partner payout mocks: the real M5 §3.5 contract (docs/modules/M5-billing.md).
//   GET  /v1/admin/payouts?week=&status=&hub_id=&cursor=&limit=  Page[PayoutOut & { partner: { display_name, kind } }]
//                                                                  admin, hub_manager (hub, read)
//   POST /v1/admin/payouts/{id}/retry                             admin; Idempotency-Key; a held or failed payout → 200 PayoutOut (pending)
// A PayoutOut has no bank details and no failure text, so the console shows neither. Weeks are ISO weeks, Monday to
// Sunday in IST, relative to today, so the newest week is always the one the latest Monday run paid.
import { HttpResponse, http } from "msw";
import { recentWeeks, runAfter } from "../lib/payout-weeks.js";
import { deny } from "./adminShared.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { PARTNER_ID } from "./partner-ids.js";
import { idempotent, mockStore, mockUuid, problem } from "./util.js";

/** @typedef {"held" | "pending" | "processing" | "processed" | "failed"} PayoutStatus */
/**
 * @typedef {{ id: string, partner_id: string, hub_id: string, week: string, period: { from: string, to: string },
 *   amount_paise: number, status: PayoutStatus, hold_reason: "kyc_incomplete" | "suspended" | "no_payout_account" | null,
 *   provider: "simulated", provider_ref: string | null, created_at: string, processed_at: string | null,
 *   partner: { display_name: string, kind: "kitchen" | "captain" | "rider" | "packer" } }} Payout
 */

/** Per partner, the amount of each of the last three weeks (newest first) at the seeded rates, and the newest week's state. */
const PEOPLE = /** @type {const} */ ([
  ["lata", "Lata tai", "kitchen", [282_000, 264_000, 234_000], "processed"],
  ["kavita", "Kavita", "packer", [48_000, 44_800, 38_400], "processed"],
  ["meena", "Meena tai", "captain", [129_000, 118_800, 96_000], "processed"],
  ["arjun", "Arjun", "rider", [120_000, 120_000, 96_000], "failed"],
]);

const payoutsStore = () =>
  mockStore("payouts-v2", () => {
    const weeks = recentWeeks(Date.now(), 3);
    /** @type {Payout[]} */
    const out = [];
    let n = 0;
    weeks.forEach((w, i) => {
      const run = runAfter(w);
      for (const [key, name, kind, amounts, newest] of PEOPLE) {
        n += 1;
        const status = i === 0 ? newest : "processed";
        out.push({
          id: mockUuid("b340", n),
          partner_id: PARTNER_ID[key],
          hub_id: KHARGHAR_HUB_ID,
          week: w.week,
          period: { from: w.from, to: w.to },
          amount_paise: amounts[i],
          status,
          hold_reason: null,
          provider: "simulated",
          provider_ref:
            status === "processed" ? `pout_sim_${n.toString(16).padStart(6, "0")}` : null,
          created_at: run,
          processed_at: status === "processed" ? run : null,
          partner: { display_name: name, kind },
        });
      }
    });
    return out;
  });

export const payoutsHandlers = [
  http.get("*/v1/admin/payouts", ({ request }) => {
    const refused = deny(request, ["admin", "hub_manager"]);
    if (refused) return refused;
    const url = new URL(request.url);
    const week = url.searchParams.get("week") ?? payoutsStore()[0]?.week;
    const status = url.searchParams.get("status");
    const hub = url.searchParams.get("hub_id");
    const items = payoutsStore().filter(
      (p) => p.week === week && (!status || p.status === status) && (!hub || p.hub_id === hub),
    );
    return HttpResponse.json({ items, next_cursor: null });
  }),

  http.post(
    "*/v1/admin/payouts/:id/retry",
    ({ request, params }) =>
      deny(request, ["admin"]) ??
      idempotent(request, () => {
        const p = payoutsStore().find((x) => x.id === params.id);
        if (!p) return problem(404, "billing.payout_not_found", "No such payout");
        if (p.status !== "failed" && p.status !== "held") {
          return problem(
            409,
            "billing.payout_not_retryable",
            "Only a held or failed payout can be retried",
          );
        }
        p.status = "pending";
        p.hold_reason = null;
        return HttpResponse.json(p);
      }),
  ),
];
