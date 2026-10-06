// Partner mocks (M8): the review queue, active partners, their profile and the fairness dashboard for Kharghar hub.
// Paths follow docs/modules/M8-partners.md; the ones the spec lacks are marked "proposal". Documents are never Aadhaar,
// and numbers are only ever returned masked ("••••4F"). Seed people: Lata Shinde (kitchen, ₹70 a tiffin), Arjun Pawar
// (rider), Meena Jadhav (captain, Sunrise Towers) and Kavita More (packer); the applicants are made-up samples.
import { HttpResponse, http } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { idempotent, mockStore, problem, roleOf } from "./util.js";

const HUB_ID = KHARGHAR_HUB_ID;
const NOW = "2026-10-06T09:40:00+05:30";
const THALI_PAISE = 7000;

/** @typedef {"kitchen" | "captain" | "rider" | "packer"} PartnerKind */
/** @type {Record<PartnerKind, string>} */
const KIND_LABEL = {
  kitchen: "Kitchen partner",
  captain: "Society Captain",
  rider: "Rider",
  packer: "Packer",
};

/**
 * @param {string} week @param {number} units @param {number} paid_paise
 */
const payout = (week, units, paid_paise) => ({ week, units, paid_paise });

/** Active partners with everything the profile page shows. */
const partnersStore = () =>
  mockStore(
    "partners-list",
    () =>
      /** @type {any[]} */ ([
        {
          id: "p-lata",
          name: "Lata Shinde",
          kind: /** @type {PartnerKind} */ ("kitchen"),
          hub_id: HUB_ID,
          society: "Kharghar",
          status: /** @type {"active" | "suspended"} */ ("active"),
          rating: 4.7,
          since: "2026-09-12",
          language: "Marathi",
          training: { done: 5, total: 5, note: "refresher due 31 Oct" },
          public_card: {
            kitchen: "Kharghar",
            specialty: "Maharashtrian thali, Jain thali",
            fssai: true,
            hygiene: true,
          },
          stats: [
            { label: "Tiffins this week", value: "34", hint: "Mon and Tue lunch" },
            { label: "Earned this week", value: "₹2,380", hint: "Paid Monday" },
            { label: "Families’ rating", value: "4.7", hint: "Last 30 days" },
            { label: "Accepted on time", value: "100%", hint: "Counts accepted within the window" },
          ],
          facts: [
            { k: "FSSAI registration", v: "••••••••••3318" },
            { k: "Training", v: "5 of 5 · refresher due 31 Oct" },
            { k: "Capacity, lunch", v: "Up to 20 a day · set daily by Lata" },
            { k: "Skills", v: "Maharashtrian, chapati-bhaji, Jain, puran poli" },
            { k: "Bank", v: "••••7731 (simulated)" },
          ],
          payouts: {
            unit_label: "Tiffins",
            items: [
              payout("28 Sep", 47, 47 * THALI_PAISE),
              payout("21 Sep", 44, 44 * THALI_PAISE),
              payout("14 Sep", 39, 39 * THALI_PAISE),
            ],
          },
          suspended_reason: null,
        },
        {
          id: "p-arjun",
          name: "Arjun Pawar",
          kind: /** @type {PartnerKind} */ ("rider"),
          hub_id: HUB_ID,
          society: "Kharghar",
          status: "active",
          rating: 4.8,
          since: "2026-09-14",
          language: "Marathi, Hindi",
          training: { done: 4, total: 4, note: "road safety refresher due 15 Nov" },
          public_card: null,
          stats: [
            { label: "Routes this week", value: "2", hint: "Mon and Tue morning" },
            { label: "Earned this week", value: "₹1,900", hint: "Paid Monday" },
            { label: "On-time stops", value: "96%", hint: "Last 30 days" },
            { label: "Handovers scanned", value: "100%", hint: "Crates and bags counted" },
          ],
          facts: [
            { k: "Vehicle", v: "Van 1 · MH-46 BX 2041" },
            { k: "Licence", v: "••••6209" },
            { k: "Training", v: "4 of 4 · road safety refresher due 15 Nov" },
            { k: "Bank", v: "••••4410 (simulated)" },
          ],
          payouts: {
            unit_label: "Routes",
            items: [
              payout("28 Sep", 6, 570000),
              payout("21 Sep", 6, 570000),
              payout("14 Sep", 5, 475000),
            ],
          },
          suspended_reason: null,
        },
        {
          id: "p-meena",
          name: "Meena Jadhav",
          kind: /** @type {PartnerKind} */ ("captain"),
          hub_id: HUB_ID,
          society: "Sunrise Towers",
          status: "active",
          rating: 4.9,
          since: "2026-09-10",
          language: "Marathi, Hindi",
          training: { done: 5, total: 5, note: "refresher due 31 Oct" },
          public_card: null,
          stats: [
            { label: "Bags handed out this week", value: "92", hint: "Mon and Tue" },
            { label: "Earned this week", value: "₹1,380", hint: "Paid Monday" },
            { label: "Flats served", value: "46", hint: "Tuesday’s boxes" },
            { label: "Problems raised", value: "1", hint: "Eggs broken, B-1104" },
          ],
          facts: [
            { k: "Society", v: "Sunrise Towers · gate 2" },
            { k: "Photo ID (PAN)", v: "••••8C" },
            { k: "Training", v: "5 of 5 · refresher due 31 Oct" },
            { k: "Bank", v: "••••2285 (simulated)" },
          ],
          payouts: {
            unit_label: "Bags",
            items: [
              payout("28 Sep", 215, 322500),
              payout("21 Sep", 198, 297000),
              payout("14 Sep", 160, 240000),
            ],
          },
          suspended_reason: null,
        },
        {
          id: "p-kavita",
          name: "Kavita More",
          kind: /** @type {PartnerKind} */ ("packer"),
          hub_id: HUB_ID,
          society: "Kharghar",
          status: "active",
          rating: 4.6,
          since: "2026-09-16",
          language: "Marathi, Hindi",
          training: { done: 3, total: 3, note: "no refresher due" },
          public_card: null,
          stats: [
            { label: "Boxes packed this week", value: "58", hint: "Mon and Tue" },
            { label: "Earned this week", value: "₹1,160", hint: "Paid Monday" },
            { label: "Shortages reported", value: "2", hint: "Both fixed before the van left" },
            { label: "Packing errors", value: "0", hint: "Last 30 days" },
          ],
          facts: [
            { k: "Shift", v: "6:00 to 8:00 AM, packing room" },
            { k: "Photo ID (PAN)", v: "••••2D" },
            { k: "Training", v: "3 of 3 · no refresher due" },
            { k: "Bank", v: "••••9012 (simulated)" },
          ],
          payouts: {
            unit_label: "Boxes",
            items: [
              payout("28 Sep", 120, 240000),
              payout("21 Sep", 112, 224000),
              payout("14 Sep", 96, 192000),
            ],
          },
          suspended_reason: null,
        },
      ]),
  );

/** @typedef {{ kind: string, label: string, masked?: string, state: "ok" | "look" | "missing", state_text: string }} Check */

/** Applications waiting at the hub; sample people, the seed has none pending. */
const applicationsStore = () =>
  mockStore("partners-applications", () => [
    {
      id: "app-sunita",
      name: "Sunita Patil",
      kind: /** @type {PartnerKind} */ ("kitchen"),
      society: "Green Valley Heights",
      scope: "would cook for Kharghar hub",
      status: /** @type {"in_review" | "kyc_pending"} */ ("in_review"),
      submitted_at: "2026-10-03T10:20:00+05:30",
      training: { done: 5, total: 5 },
      checks: /** @type {Check[]} */ ([
        {
          kind: "photo_id",
          label: "Photo ID (PAN)",
          masked: "••••4F",
          state: "ok",
          state_text: "Checked (simulated)",
        },
        {
          kind: "fssai",
          label: "FSSAI registration",
          masked: "••••••••••3271",
          state: "ok",
          state_text: "Received",
        },
        {
          kind: "bank",
          label: "Bank account · penny drop",
          state: "ok",
          state_text: "Matched (simulated)",
        },
        {
          kind: "training",
          label: "Training · 5 modules, quiz average 4.6 of 5",
          state: "ok",
          state_text: "Done",
        },
        { kind: "photos", label: "Kitchen photos · 3", state: "look", state_text: "Look at them" },
      ]),
    },
    {
      id: "app-rekha",
      name: "Rekha Kamble",
      kind: /** @type {PartnerKind} */ ("captain"),
      society: "Shree Ganesh CHS",
      scope: "would be Society Captain of Shree Ganesh CHS",
      status: "in_review",
      submitted_at: "2026-10-04T16:05:00+05:30",
      training: { done: 5, total: 5 },
      checks: [
        {
          kind: "photo_id",
          label: "Photo ID (PAN)",
          masked: "••••9B",
          state: "ok",
          state_text: "Checked (simulated)",
        },
        {
          kind: "bank",
          label: "Bank account · penny drop",
          state: "ok",
          state_text: "Matched (simulated)",
        },
        {
          kind: "training",
          label: "Training · 5 modules, quiz average 4.2 of 5",
          state: "ok",
          state_text: "Done",
        },
        {
          kind: "nomination",
          label: "Nominated by the society committee",
          state: "look",
          state_text: "Look at the letter",
        },
      ],
    },
    {
      id: "app-anita",
      name: "Anita Gaikwad",
      kind: /** @type {PartnerKind} */ ("kitchen"),
      society: "Sai Residency",
      scope: "would cook for Kharghar hub",
      status: "kyc_pending",
      submitted_at: "2026-10-02T11:40:00+05:30",
      training: { done: 3, total: 5 },
      checks: [
        {
          kind: "photo_id",
          label: "Photo ID (PAN)",
          masked: "••••7A",
          state: "ok",
          state_text: "Checked (simulated)",
        },
        {
          kind: "fssai",
          label: "FSSAI registration",
          state: "missing",
          state_text: "Not uploaded yet",
        },
        {
          kind: "bank",
          label: "Bank account · penny drop",
          state: "ok",
          state_text: "Matched (simulated)",
        },
        {
          kind: "training",
          label: "Training · 3 of 5 modules",
          state: "missing",
          state_text: "In progress",
        },
      ],
    },
  ]);

// ---- Fairness: how kitchen tiffins were spread last week, and the weight the nightly assignment uses -------------------

/** Week of 28 Sep. Lata Shinde is the seed kitchen at ₹70 a tiffin; the other three are samples. Gini and shares are the API's numbers. */
const fairnessStore = () =>
  mockStore("partners-fairness", () => ({
    week_start: "2026-09-28",
    gini: 0.14,
    gini_trend: [0.24, 0.21, 0.19, 0.14],
    gini_change: -0.05,
    gini_change_note: "since λ was raised",
    top_share_pct: 31,
    top_share_change_pts: -6,
    lambda: 0.3,
    lambda_changed_by: /** @type {string | null} */ ("Neha Iyer"),
    lambda_changed_at: /** @type {string | null} */ ("2026-09-22T18:10:00+05:30"),
    fallback_active: false,
    kitchens: [
      {
        partner_id: "p-lata",
        name: "Lata Shinde",
        tiffins: 47,
        capacity: 80,
        earned_paise: 47 * THALI_PAISE,
        rating: 4.7,
      },
      {
        partner_id: null,
        name: "Kitchen 2",
        tiffins: 44,
        capacity: 70,
        earned_paise: 44 * THALI_PAISE,
        rating: 4.6,
      },
      {
        partner_id: null,
        name: "Kitchen 3",
        tiffins: 38,
        capacity: 60,
        earned_paise: 38 * THALI_PAISE,
        rating: 4.8,
      },
      {
        partner_id: null,
        name: "Kitchen 4",
        tiffins: 22,
        capacity: 60,
        earned_paise: 22 * THALI_PAISE,
        rating: 4.5,
      },
    ],
  }));

/** @param {string} iso */
const daysSince = (iso) => Math.floor((Date.parse(NOW) - Date.parse(iso)) / 86_400_000);

export const partnersHandlers = [
  // GET /v1/hubs/{id}/partner-applications (M8 §3): the review queue, oldest first. `waiting_days` is computed by the API.
  http.get("*/v1/hubs/:hub/partner-applications", ({ params }) => {
    const items =
      params.hub === HUB_ID
        ? applicationsStore()
            .toSorted((a, b) => a.submitted_at.localeCompare(b.submitted_at))
            .map((a) => ({
              ...a,
              kind_label: KIND_LABEL[a.kind],
              waiting_days: daysSince(a.submitted_at),
            }))
        : [];
    return HttpResponse.json({ items, next_cursor: null });
  }),

  // GET /v1/hubs/{id}/partners (proposal): verified partners of the hub, any role.
  http.get("*/v1/hubs/:hub/partners", ({ params }) => {
    const items =
      params.hub === HUB_ID
        ? partnersStore().map((p) => ({
            id: p.id,
            name: p.name,
            kind: p.kind,
            kind_label: KIND_LABEL[/** @type {PartnerKind} */ (p.kind)],
            society: p.society,
            status: p.status,
            rating: p.rating,
            since: p.since,
            training: p.training,
          }))
        : [];
    return HttpResponse.json({ items, next_cursor: null });
  }),

  // POST /v1/partner-applications/{id}/decision (M8 §3): approve activates the role (an event to M1 in the real thing);
  // reject needs a reason the applicant sees.
  http.post("*/v1/partner-applications/:id/decision", ({ request, params }) =>
    idempotent(request, (body) => {
      const apps = applicationsStore();
      const app = apps.find((a) => a.id === params.id);
      if (!app) return problem(404, "partners.application_not_found", "No such application");
      if (body?.decision !== "approve" && body?.decision !== "reject") {
        return problem(422, "request.invalid", "Pick approve or reject", {
          errors: [{ field: "decision", message: "Pick approve or reject" }],
        });
      }
      if (body.decision === "reject" && !String(body.reason ?? "").trim()) {
        return problem(422, "request.invalid", "Say why", {
          errors: [{ field: "reason", message: "Tell the applicant why" }],
        });
      }
      if (
        body.decision === "approve" &&
        (app.status !== "in_review" || app.checks.some((c) => c.state === "missing"))
      ) {
        return problem(409, "partners.not_ready", "Documents or training are still missing");
      }
      apps.splice(apps.indexOf(app), 1);
      if (body.decision === "approve") {
        partnersStore().push({
          id: `p-${app.id.slice(4)}`,
          name: app.name,
          kind: app.kind,
          hub_id: HUB_ID,
          society: app.society,
          status: "active",
          rating: 0,
          since: NOW.slice(0, 10),
          language: "",
          training: { done: app.training.done, total: app.training.total, note: "" },
          public_card: null,
          stats: [],
          facts: app.checks.flatMap((c) => (c.masked ? [{ k: c.label, v: c.masked }] : [])),
          payouts: { unit_label: "Orders", items: [] },
          suspended_reason: null,
        });
      }
      return HttpResponse.json({
        id: app.id,
        status: body.decision === "approve" ? "approved" : "rejected",
      });
    }),
  ),
  // GET /v1/partners/{id} (proposal; M8 only has /public for the know-your-cook card): the hub manager's view of one
  // partner with this week's numbers, kitchen facts and recent payouts from the ledger. Numbers are masked.
  http.get("*/v1/partners/:id", ({ params }) => {
    const p = partnersStore().find((x) => x.id === params.id);
    return p
      ? HttpResponse.json({
          ...p,
          kind_label: KIND_LABEL[/** @type {PartnerKind} */ (p.kind)],
          hub: { id: HUB_ID, name: "Kharghar hub" },
        })
      : problem(404, "partners.not_found", "No such partner");
  }),

  // POST /v1/partners/{id}/suspend (M8 §3). The reason is optional and goes to the audit log.
  http.post("*/v1/partners/:id/suspend", ({ request, params }) =>
    idempotent(request, (body) => {
      const p = partnersStore().find((x) => x.id === params.id);
      if (!p) return problem(404, "partners.not_found", "No such partner");
      if (p.status === "suspended")
        return problem(409, "partners.already_suspended", "Already suspended");
      p.status = "suspended";
      p.suspended_reason = String(body?.reason ?? "").trim() || null;
      return HttpResponse.json({ id: p.id, status: p.status });
    }),
  ),
  // GET /v1/hubs/{id}/fairness (proposal): the earnings distribution of last week (analytics `mv_partner_earnings_distribution`,
  // M12) with the current fairness weight λ of the M10 assignment. `kitchens_under_half` counts kitchens below half their capacity.
  http.get("*/v1/hubs/:hub/fairness", ({ params }) => {
    if (params.hub !== HUB_ID) return problem(404, "hubs.not_found", "No such hub");
    const f = fairnessStore();
    return HttpResponse.json({
      ...f,
      kitchens_under_half: f.kitchens.filter((k) => k.tiffins < k.capacity / 2).length,
    });
  }),

  // PUT /v1/hubs/{id}/fairness-weight (proposal): admin only; every change is logged with who made it.
  http.put("*/v1/hubs/:hub/fairness-weight", async ({ request, params }) => {
    if (params.hub !== HUB_ID) return problem(404, "hubs.not_found", "No such hub");
    if (roleOf(request) !== "admin")
      return problem(403, "partners.admin_only", "Only an admin can change the fairness weight");
    const body = /** @type {any} */ (await request.json().catch(() => null));
    const value = Number(body?.lambda);
    if (
      body?.lambda === "" ||
      body?.lambda == null ||
      !Number.isFinite(value) ||
      value < 0 ||
      value > 1
    ) {
      return problem(422, "request.invalid", "Weight must be from 0 to 1", {
        errors: [{ field: "lambda", message: "Enter a number from 0 to 1" }],
      });
    }
    const f = fairnessStore();
    f.lambda = value;
    f.lambda_changed_by = "Neha Iyer";
    f.lambda_changed_at = NOW;
    return HttpResponse.json({
      lambda: f.lambda,
      lambda_changed_by: f.lambda_changed_by,
      lambda_changed_at: f.lambda_changed_at,
    });
  }),
];
