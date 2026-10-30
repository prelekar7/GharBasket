// Community mocks (M9 section 3.4 and the NGOs of 3.5), as the frozen contract has them: point rates, donation
// drives, the hub's donation verification queue and NGOs (offers and hand-offs are in handoffs.js). Ids are uuids, lists are
// `{ items, next_cursor }`, creating POSTs need an Idempotency-Key, roles and hub grants are checked, and the
// problem codes are the spec's. Seeded story: Tue 6 Oct 2026, Kharghar hub, the Diwali drive (2x) running,
// Arjun back from the return leg with two donations.
//
// Not in the spec (kept here, listed as backend asks): `version` on a drive or NGO for If-Match (the PATCH
// endpoints are not mocked because no page edits yet).
import { HttpResponse, http } from "msw";
import { countProblem, donationPoints } from "../lib/kindness.js";
import { HUBS, KHARGHAR_HUB_ID, visibleHubs } from "./hubs.js";
import { NGO_ID, SOCIETY_ID } from "./ids.js";
import { fieldError, idempotent, mockStore, mockUuid, problem, roleOf } from "./util.js";

/** The story's "today", so drive states do not depend on the machine's clock. */
export const TODAY = "2026-10-06";
export const NOW = "2026-10-06T11:10:00+05:30";

/** @typedef {"admin" | "hub_manager" | "franchise_owner" | "ngo_coordinator" | "catalog_editor"} Role */
/** @typedef {import("../lib/kindness.js").PointRate} PointRate */
/**
 * @typedef {{ id: string, name: string }} NgoCard
 * @typedef {{ id: string, code: string, name: string, description: string | null, scope: "platform" | "hub" | "society",
 *   hub_id: string | null, society_id: string | null, codes: string[], multiplier: number, starts_on: string,
 *   ends_on: string, cancelled: boolean, ngo: NgoCard | null, goal_items: number | null,
 *   progress: { pledged: number, verified: number } }} Drive
 * @typedef {{ id: string, name: string, kind: "food_bank" | "shelter" | "school" | "other", hub_ids: string[],
 *   accepts: ("items" | "food" | "meals")[], registration_ref: string | null, verified: boolean,
 *   status: "active" | "paused", note: string | null, coordinator: { name: string, phone_e164: string } | null }} Ngo
 * @typedef {{ code: string, unit: "kg" | "each", qty: number, counted: number | null, points: number | null }} DonationItem
 * @typedef {{ id: string, status: "scheduled" | "picked_up" | "verified" | "rejected" | "handed_over" | "cancelled",
 *   items: DonationItem[], photos: number, drive: { id: string, name: string, multiplier: number } | null,
 *   pickup_on: string, estimated_points: number, points_awarded: number | null,
 *   reject_reason: string | null, cancel_reason: string | null, ngo: { name: string } | null,
 *   timeline: { status: string, at: string }[], created_at: string,
 *   household_id: string, society: { id: string, name: string }, tower: string, flat: string,
 *   picked_up_by: { name: string } | null, picked_up_on: string | null }} Donation
 */

/** @type {PointRate[]} */
const RATES = [
  { code: "clothes", label: "Clothes", unit: "kg", points: 40, max_qty: 15 },
  { code: "utensils", label: "Utensils", unit: "kg", points: 30, max_qty: 10 },
  { code: "school_bag", label: "School bags", unit: "each", points: 50, max_qty: 5 },
  { code: "books", label: "Books", unit: "each", points: 10, max_qty: 30 },
  { code: "toys", label: "Toys", unit: "each", points: 15, max_qty: 10 },
];
const ALL_CODES = RATES.map((r) => r.code);

/** A small grey square that stands in for a donation photo behind its 5-minute link. */
const PHOTO = `data:image/svg+xml,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="160"><rect width="240" height="160" fill="#ddd"/><text x="120" y="86" text-anchor="middle" font-size="14" fill="#666">photo</text></svg>',
)}`;
export const photoUrls = (/** @type {number} */ n) =>
  Array.from({ length: n }, () => ({ url: PHOTO, expires_at: "2026-10-06T11:15:00+05:30" }));

const NGO1 = { id: NGO_ID.first, name: "Partner NGO 1" };
const DIWALI = { id: mockUuid("b900", 1), name: "Diwali drive", multiplier: 2 };
const SUNRISE = { id: SOCIETY_ID["sunrise-towers"], name: "Sunrise Towers" };
const KRISHNA = { id: SOCIETY_ID["krishna-kunj"], name: "Krishna Kunj" };

/** @returns {Drive[]} */
const seedDrives = () => [
  {
    id: DIWALI.id,
    code: "diwali-2026",
    name: "Diwali drive",
    description: "Clothes, books, toys, school bags, utensils",
    scope: "platform",
    hub_id: null,
    society_id: null,
    codes: ALL_CODES,
    multiplier: 2,
    starts_on: "2026-10-01",
    ends_on: "2026-10-31",
    cancelled: false,
    ngo: NGO1,
    goal_items: 200,
    progress: { pledged: 40, verified: 31 },
  },
  {
    id: mockUuid("b900", 2),
    code: "back-to-school-2026",
    name: "Back to school",
    description: "Notebooks, school bags and stationery",
    scope: "platform",
    hub_id: null,
    society_id: null,
    codes: ["school_bag", "books"],
    multiplier: 1.5,
    starts_on: "2026-06-10",
    ends_on: "2026-06-30",
    cancelled: false,
    ngo: null,
    goal_items: null,
    progress: { pledged: 64, verified: 58 },
  },
];

/** @returns {Ngo[]} */
const seedNgos = () => [
  {
    id: NGO_ID.first,
    name: "Partner NGO 1",
    kind: "food_bank",
    hub_ids: [KHARGHAR_HUB_ID],
    accepts: ["items", "food", "meals"],
    registration_ref: "DARPAN MH/2019/0123456",
    verified: true,
    status: "active",
    note: "Name to be confirmed",
    coordinator: { name: "Asha Fernandes", phone_e164: "+919820000011" },
  },
  {
    id: NGO_ID.second,
    name: "Partner NGO 2",
    kind: "other",
    hub_ids: [KHARGHAR_HUB_ID],
    accepts: ["items"],
    registration_ref: null,
    verified: false,
    status: "active",
    note: "Being identified",
    coordinator: null,
  },
];

/**
 * @param {number} n
 * @param {string} status
 * @param {Partial<Donation> & Pick<Donation, "items" | "society" | "tower" | "flat" | "pickup_on">} d
 * @returns {Donation}
 */
const donation = (n, status, d) => ({
  id: mockUuid("ba00", n),
  status: /** @type {Donation["status"]} */ (status),
  photos: 2,
  drive: DIWALI,
  estimated_points: 0,
  points_awarded: null,
  reject_reason: null,
  cancel_reason: null,
  ngo: null,
  timeline: [
    { status: "scheduled", at: "2026-10-03T19:40:00+05:30" },
    { status: "picked_up", at: `${d.pickup_on}T09:05:00+05:30` },
  ],
  created_at: "2026-10-03T19:40:00+05:30",
  household_id: mockUuid("bd00", n),
  picked_up_by: { name: "Arjun Pawar" },
  picked_up_on: d.pickup_on,
  ...d,
});

/** @returns {Donation[]} */
const seedDonations = () => [
  donation(1, "picked_up", {
    society: SUNRISE,
    tower: "Wing C",
    flat: "807",
    pickup_on: "2026-10-06",
    items: [
      { code: "toys", unit: "each", qty: 4, counted: null, points: null },
      { code: "books", unit: "each", qty: 6, counted: null, points: null },
    ],
    estimated_points: 240,
  }),
  donation(2, "picked_up", {
    society: KRISHNA,
    tower: "Wing A",
    flat: "101",
    photos: 1,
    pickup_on: "2026-10-06",
    items: [{ code: "books", unit: "each", qty: 12, counted: null, points: null }],
    estimated_points: 240,
  }),
  donation(3, "verified", {
    society: SUNRISE,
    tower: "Wing B",
    flat: "402",
    pickup_on: "2026-09-29",
    items: [
      { code: "school_bag", unit: "each", qty: 1, counted: 1, points: 100 },
      { code: "clothes", unit: "kg", qty: 3, counted: 3, points: 240 },
    ],
    estimated_points: 340,
    points_awarded: 340,
  }),
];

export const communityStore = () =>
  mockStore("community", () => ({
    drives: seedDrives(),
    ngos: seedNgos(),
    donations: seedDonations(),
  }));

/**
 * Who may call, as a problem or null. 401 signed out, 403 `auth.forbidden` for any other role.
 * @param {Request} request
 * @param {Role[]} roles
 */
export function need(request, roles) {
  const role = roleOf(request);
  if (!role) return problem(401, "auth.unauthenticated", "Sign in again");
  return roles.includes(role) ? null : problem(403, "auth.forbidden", "You cannot do this");
}

/** A hub manager works in their own hub; admins in any. @param {Request} request @param {string | null} hubId */
export function hubDenied(request, hubId) {
  if (!hubId) return null;
  if (!HUBS.some((h) => h.id === hubId))
    return problem(404, "hub_ops.hub_not_found", "No such hub");
  return visibleHubs(request).some((h) => h.id === hubId)
    ? null
    : problem(403, "auth.forbidden", "You do not have access to this hub");
}

/** The cursor page of the contract. @template T @param {T[]} items */
export const page = (items) => HttpResponse.json({ items, next_cursor: null });

/** `state` is computed from the dates in IST, except `cancelled`. @param {Drive} d */
function driveOut(d) {
  const { cancelled, ...rest } = d;
  const state = cancelled
    ? "cancelled"
    : d.ends_on < TODAY
      ? "ended"
      : d.starts_on > TODAY
        ? "upcoming"
        : "running";
  return { ...rest, state };
}

/** @param {unknown} v */
const isDate = (v) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v);

/** @param {Donation} d @returns {Donation} */
const donationOut = (d) => ({ ...d });

/** What the hub sees of a donation in its queue: the donor's place, never a name or phone. @param {Donation} d */
const queueOut = (d) => ({ ...donationOut(d), photo_urls: photoUrls(d.photos) });

const NGO_KINDS = ["food_bank", "shelter", "school", "other"];
const REJECT_REASONS = ["unusable", "not_accepted", "unsafe", "other"];

export const communityHandlers = [
  // GET /v1/point-rates (any signed-in user)
  http.get("*/v1/point-rates", ({ request }) =>
    roleOf(request)
      ? HttpResponse.json({ items: RATES })
      : problem(401, "auth.unauthenticated", "Sign in again"),
  ),

  // GET /v1/drives?state= : default running and upcoming; staff may ask for any state
  http.get("*/v1/drives", ({ request }) => {
    const denied = need(request, ["admin", "hub_manager", "franchise_owner", "ngo_coordinator"]);
    if (denied) return denied;
    const state = new URL(request.url).searchParams.get("state");
    const items = communityStore()
      .drives.map(driveOut)
      .filter((d) => (state ? d.state === state : d.state === "running" || d.state === "upcoming"))
      .sort((a, b) => b.starts_on.localeCompare(a.starts_on));
    return page(items);
  }),

  // POST /v1/admin/drives (Idempotency-Key; a hub manager only for their hub or a society in it)
  http.post("*/v1/admin/drives", ({ request }) => {
    const denied = need(request, ["admin", "hub_manager"]);
    if (denied) return denied;
    return idempotent(request, (body) => {
      if (
        roleOf(request) === "hub_manager" &&
        (body?.scope === "platform" || body?.hub_id !== KHARGHAR_HUB_ID)
      ) {
        return problem(403, "auth.forbidden", "A hub manager can start drives for their own hub");
      }
      const errors = [];
      const text = (/** @type {any} */ v) => String(v?.en ?? "").trim();
      if (!text(body?.name)) errors.push(fieldError("name", "Give the drive a name", "missing"));
      if (!String(body?.code ?? "").trim())
        errors.push(fieldError("code", "A code is needed", "missing"));
      if (!isDate(body?.starts_on))
        errors.push(fieldError("starts_on", "Pick a start date", "missing"));
      if (!isDate(body?.ends_on)) errors.push(fieldError("ends_on", "Pick an end date", "missing"));
      const mult = Number(body?.multiplier);
      if (!(mult >= 1 && mult <= 3 && Number.isInteger(mult * 10)))
        errors.push(fieldError("multiplier", "1.0 to 3.0, one decimal"));
      if (!["platform", "hub", "society"].includes(body?.scope))
        errors.push(fieldError("scope", "Pick who the drive is for"));
      const codes = Array.isArray(body?.codes) ? body.codes : [];
      if (codes.length === 0 || codes.some((/** @type {string} */ c) => !ALL_CODES.includes(c)))
        errors.push(fieldError("codes", "Pick known item categories", "value_error"));
      if (errors.length) return problem(422, "request.invalid", "Check the form", { errors });
      if (body.ends_on < body.starts_on || body.starts_on < TODAY) {
        return problem(422, "community.drive_dates_invalid", "Check the dates of the drive", {
          errors: [
            fieldError(body.ends_on < body.starts_on ? "ends_on" : "starts_on", "Pick valid dates"),
          ],
        });
      }
      const s = communityStore();
      const ngo = s.ngos.find((n) => n.id === body.ngo_id);
      /** @type {Drive} */
      const drive = {
        id: mockUuid("b900", s.drives.length + 1),
        code: body.code,
        name: text(body.name),
        description: text(body.description) || null,
        scope: body.scope,
        hub_id: body.hub_id ?? null,
        society_id: body.society_id ?? null,
        codes,
        multiplier: mult,
        starts_on: body.starts_on,
        ends_on: body.ends_on,
        cancelled: false,
        ngo: ngo ? { id: ngo.id, name: ngo.name } : null,
        goal_items: body.goal_items ?? null,
        progress: { pledged: 0, verified: 0 },
      };
      s.drives.unshift(drive);
      return HttpResponse.json(driveOut(drive), { status: 201 });
    });
  }),

  // GET /v1/ngos?hub_id= : staff of a hub and admins; an NGO coordinator gets their own
  http.get("*/v1/ngos", ({ request }) => {
    const denied = need(request, ["admin", "hub_manager", "ngo_coordinator"]);
    if (denied) return denied;
    const hub = new URL(request.url).searchParams.get("hub_id");
    const bad = hubDenied(request, hub);
    if (bad) return bad;
    const role = roleOf(request);
    return page(
      communityStore().ngos.filter((n) =>
        role === "ngo_coordinator" ? n.id === NGO_ID.first : !hub || n.hub_ids.includes(hub),
      ),
    );
  }),

  // POST /v1/admin/ngos (admin, Idempotency-Key). A coordinator is a role grant (M1), not a field.
  http.post("*/v1/admin/ngos", ({ request }) => {
    const denied = need(request, ["admin"]);
    if (denied) return denied;
    return idempotent(request, (body) => {
      const errors = [];
      if (!String(body?.name ?? "").trim())
        errors.push(fieldError("name", "Give the NGO a name", "missing"));
      if (!NGO_KINDS.includes(body?.kind))
        errors.push(fieldError("kind", "Pick what kind of NGO it is"));
      if (errors.length) return problem(422, "request.invalid", "Check the form", { errors });
      const s = communityStore();
      /** @type {Ngo} */
      const ngo = {
        id: mockUuid("b800", s.ngos.length + 1),
        name: body.name.trim(),
        kind: body.kind,
        hub_ids: body.hub_ids ?? [],
        accepts: body.accepts ?? ["items"],
        registration_ref: String(body.registration_ref ?? "").trim() || null,
        verified: false,
        status: "active",
        note: body.note ?? null,
        coordinator: null,
      };
      s.ngos.push(ngo);
      return HttpResponse.json(ngo, { status: 201 });
    });
  }),

  // GET /v1/admin/donations?hub_id=&status=picked_up : oldest first, with the donor's place and 5-minute photo links
  http.get("*/v1/admin/donations", ({ request }) => {
    const denied = need(request, ["admin", "hub_manager"]);
    if (denied) return denied;
    const q = new URL(request.url).searchParams;
    const bad = hubDenied(request, q.get("hub_id"));
    if (bad) return bad;
    const status = q.get("status");
    const items = communityStore()
      .donations.filter((d) => !status || d.status === status)
      .sort(
        (a, b) =>
          (a.picked_up_on ?? "").localeCompare(b.picked_up_on ?? "") || a.id.localeCompare(b.id),
      );
    return page(items.map(queueOut));
  }),

  // POST /v1/donations/{id}/verify : counted by code; the server decides the points
  http.post("*/v1/donations/:id/verify", ({ request, params }) => {
    const denied = need(request, ["admin", "hub_manager"]);
    if (denied) return denied;
    return idempotent(request, (body) => {
      const d = communityStore().donations.find((x) => x.id === params.id);
      if (!d) return problem(404, "community.donation_not_found", "No such donation");
      if (d.status === "verified" || d.status === "rejected")
        return problem(
          409,
          "community.donation_already_verified",
          "This donation is already done",
          { status: d.status },
        );
      if (d.status !== "picked_up")
        return problem(409, "community.donation_not_picked_up", "It has not been picked up yet");
      /** @type {Record<string, number>} */
      const counted = body?.counted ?? {};
      const errors = [];
      for (const [code, qty] of Object.entries(counted)) {
        const rate = RATES.find((r) => r.code === code);
        if (!rate) return problem(422, "community.item_code_unknown", "Unknown item category");
        if (countProblem(rate, Number(qty)))
          errors.push(fieldError(["counted", code], countProblem(rate, Number(qty)) ?? ""));
      }
      if (errors.length)
        return problem(422, "community.donation_qty_invalid", "Check the counted quantities", {
          errors,
        });
      if (!Object.values(counted).some((q) => Number(q) > 0))
        return problem(
          422,
          "community.nothing_counted",
          "Nothing was counted; reject the donation instead",
        );
      const drive = d.drive ? communityStore().drives.find((x) => x.id === d.drive?.id) : null;
      const points = donationPoints(counted, RATES, drive ?? null);
      for (const [code, qty] of Object.entries(counted)) {
        const line = d.items.find((i) => i.code === code);
        if (line) {
          line.counted = Number(qty);
          line.points = points.lines[code];
        } else {
          const rate = /** @type {PointRate} */ (RATES.find((r) => r.code === code));
          d.items.push({
            code,
            unit: rate.unit,
            qty: 0,
            counted: Number(qty),
            points: points.lines[code],
          });
        }
      }
      d.status = "verified";
      d.points_awarded = points.total;
      d.timeline.push({ status: "verified", at: NOW });
      return HttpResponse.json(donationOut(d));
    });
  }),

  // POST /v1/donations/{id}/reject : earns nothing, the items are not returned
  http.post("*/v1/donations/:id/reject", ({ request, params }) => {
    const denied = need(request, ["admin", "hub_manager"]);
    if (denied) return denied;
    return idempotent(request, (body) => {
      const d = communityStore().donations.find((x) => x.id === params.id);
      if (!d) return problem(404, "community.donation_not_found", "No such donation");
      if (d.status === "verified" || d.status === "rejected")
        return problem(
          409,
          "community.donation_already_verified",
          "This donation is already done",
          { status: d.status },
        );
      if (d.status !== "picked_up")
        return problem(409, "community.donation_not_picked_up", "It has not been picked up yet");
      if (!REJECT_REASONS.includes(body?.reason))
        return problem(422, "request.invalid", "Pick a reason", {
          errors: [fieldError("reason", "Pick a reason")],
        });
      d.status = "rejected";
      d.reject_reason = body.reason;
      d.timeline.push({ status: "rejected", at: NOW });
      return HttpResponse.json(donationOut(d));
    });
  }),
];
