// Vendor mocks (M6 §3 and §4: vendors and weekly vendor_scores). Spec: GET / POST / PATCH
// /v1/hubs/{id}/vendors (POST needs an Idempotency-Key). The scoring job (freshness 40 %, price 20 %,
// reliability 20 %, defects 20 %) belongs to the backend; the mock only carries the numbers the Vendors
// design shows for the week of 28 Sep 2026. The score columns and `history` are our proposal; the spec
// lists freshness, price, reliability, defects and score per week.
import { HttpResponse, http } from "msw";
import { procurementState, VENDORS } from "./procurement.js";
import { idempotent, mockStore, problem } from "./util.js";

const WEEKS = ["2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"];
const KINDS = ["apmc", "fpo", "farmer"];

/**
 * @typedef {{ id: string, name: string, supplies: string, kind: string, phone: string,
 *   scores: { ontime_pct: number, fill_pct: number, grade_a_pct: number, score: number,
 *     state: { tone: "primary" | "accent", label: string } } | null,
 *   history: { week: string, fill_pct: number, grade_a_pct: number }[] }} Vendor
 */

/** @type {Record<string, Vendor["scores"] & {}>} */
const SCORES = {
  "vendor-a": {
    ontime_pct: 96,
    fill_pct: 98,
    grade_a_pct: 91,
    score: 94,
    state: { tone: "primary", label: "Preferred" },
  },
  "vendor-b": {
    ontime_pct: 88,
    fill_pct: 84,
    grade_a_pct: 86,
    score: 86,
    state: { tone: "accent", label: "Watch: methi short twice" },
  },
  "vendor-c": {
    ontime_pct: 100,
    fill_pct: 97,
    grade_a_pct: 95,
    score: 97,
    state: { tone: "primary", label: "Preferred" },
  },
};
/** [fill %, grade A %] for the four weeks. */
const HISTORY = {
  "vendor-a": [
    [97, 90],
    [98, 92],
    [96, 89],
    [98, 91],
  ],
  "vendor-b": [
    [92, 89],
    [90, 88],
    [88, 87],
    [84, 86],
  ],
  "vendor-c": [
    [99, 96],
    [97, 94],
    [98, 95],
    [97, 95],
  ],
};

const vendors = () =>
  mockStore(
    "vendors",
    () =>
      /** @type {Vendor[]} */ (
        VENDORS.map((v) => ({
          ...v,
          scores: SCORES[v.id],
          history: HISTORY[/** @type {keyof typeof HISTORY} */ (v.id)].map(([fill, gradeA], i) => ({
            week: WEEKS[i],
            fill_pct: fill,
            grade_a_pct: gradeA,
          })),
        }))
      ),
  );

export const vendorsHandlers = [
  http.get("*/v1/hubs/:id/vendors", ({ params }) => {
    if (params.id !== "kharghar") return problem(404, "hubs.not_found", "No such hub");
    const lines = procurementState().plan.lines;
    return HttpResponse.json({
      week: WEEKS.at(-1),
      weeks: WEEKS,
      items: vendors().map((v) => ({
        ...v,
        item_count: lines.filter((l) => l.vendor_id === v.id).length,
      })),
      next_cursor: null,
    });
  }),

  http.post("*/v1/hubs/:id/vendors", ({ params, request }) =>
    idempotent(request, (body) => {
      if (params.id !== "kharghar") return problem(404, "hubs.not_found", "No such hub");
      const name = String(body?.name ?? "").trim();
      const supplies = String(body?.supplies ?? "").trim();
      const phone = String(body?.phone ?? "").trim();
      /** @type {{ field: string, message: string }[]} */
      const errors = [];
      if (!name) errors.push({ field: "name", message: "Enter the vendor’s name" });
      if (!supplies) errors.push({ field: "supplies", message: "Say what they supply" });
      if (!/^[6-9]\d{9}$/.test(phone))
        errors.push({ field: "phone", message: "Enter a 10-digit mobile number" });
      if (!KINDS.includes(body?.kind))
        errors.push({ field: "kind", message: "Choose the kind of vendor" });
      if (errors.length)
        return problem(422, "request.invalid", "Check the vendor’s details", { errors });
      if (vendors().some((v) => v.name.toLowerCase() === name.toLowerCase()))
        return problem(409, "vendors.name_taken", "A vendor with this name already exists");
      /** @type {Vendor} */
      const vendor = {
        id: `vendor-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`,
        name,
        supplies,
        kind: body.kind,
        phone,
        scores: null,
        history: [],
      };
      vendors().push(vendor);
      return HttpResponse.json({ ...vendor, item_count: 0 }, { status: 201 });
    }),
  ),
];
