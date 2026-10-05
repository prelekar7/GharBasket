// Community mocks (M9): donation drives, points per item, NGOs and donation verification. The M9 spec
// names `GET /v1/drives`, `POST /v1/donations/{id}/verify` and `POST /v1/ngo-handoffs`; the admin
// list and create paths below are our proposal. Seeded story: Tue 6 Oct 2026, Kharghar hub, the Diwali
// drive (2x) running, Arjun back from the return leg with two donations.
import { HttpResponse, http } from "msw";
import { idempotent, mockStore, problem, roleOf } from "./util.js";

/** The story's "today", so drive states do not depend on the machine's clock. */
export const TODAY = "2026-10-06";

/**
 * @typedef {object} Drive `community.donation_drives` plus a name and the items it asks for.
 * @property {string} id
 * @property {string} name
 * @property {string} description
 * @property {string} starts_on ISO date
 * @property {string} ends_on ISO date
 * @property {number} multiplier points multiplier while the drive runs
 * @property {"upcoming" | "running" | "ended"} [state] set by the API from the dates
 *
 * @typedef {object} PointRate Base Kindness points for one kind of item (before the drive multiplier).
 * @property {string} key
 * @property {string} label
 * @property {"kg" | "each"} unit
 * @property {number} points per unit
 *
 * @typedef {object} Ngo `community.ngos`
 * @property {string} id
 * @property {string} name
 * @property {string | null} note
 * @property {string | null} coordinator
 * @property {boolean} verified unverified NGOs cannot receive hand-offs
 *
 * @typedef {object} DonationLine
 * @property {string} key a PointRate key
 * @property {string} label
 * @property {"kg" | "each"} unit
 * @property {number} said what the family declared
 * @property {number | null} counted what the hub counted, once verified
 * @property {number} rate base points per unit when the donation was scheduled
 *
 * @typedef {object} Donation `community.donations`, as the hub sees it: flats only, never the
 * donor's name or phone.
 * @property {string} id
 * @property {string} flat
 * @property {string} society
 * @property {DonationLine[]} lines
 * @property {number} photos
 * @property {string} picked_up_on ISO date
 * @property {string} picked_up_by
 * @property {{ id: string, name: string, multiplier: number }} drive
 * @property {"to_verify" | "verified"} status
 * @property {number | null} points_awarded
 * @property {boolean} has_unsuitable
 */

/** @type {Drive[]} */
const seedDrives = [
  {
    id: "diwali",
    name: "Diwali drive",
    description: "Clothes, books, toys, school bags, utensils",
    starts_on: "2026-10-01",
    ends_on: "2026-10-31",
    multiplier: 2,
  },
  {
    id: "school",
    name: "Back to school",
    description: "Notebooks, school bags and stationery",
    starts_on: "2026-06-10",
    ends_on: "2026-06-30",
    multiplier: 1.5,
  },
];

/** @type {PointRate[]} */
const RATES = [
  { key: "clothes", label: "Clothes", unit: "kg", points: 40 },
  { key: "school_bag", label: "School bags", unit: "each", points: 50 },
  { key: "books", label: "Books", unit: "each", points: 10 },
  { key: "toys", label: "Toys", unit: "each", points: 15 },
  { key: "utensils", label: "Utensils", unit: "kg", points: 30 },
];

/** @type {Ngo[]} */
const seedNgos = [
  {
    id: "ngo-1",
    name: "Partner NGO 1",
    note: "Name to be confirmed",
    coordinator: "Asha Fernandes",
    verified: true,
  },
  {
    id: "ngo-2",
    name: "Partner NGO 2",
    note: "Being identified",
    coordinator: null,
    verified: false,
  },
];

const diwali = { id: "diwali", name: "Diwali drive", multiplier: 2 };

/** @type {Donation[]} */
const seedDonations = [
  {
    id: "don-c807",
    flat: "C-807",
    society: "Sunrise Towers",
    lines: [
      { key: "toys", label: "Toys", unit: "each", said: 4, counted: null, rate: 15 },
      { key: "books", label: "Books", unit: "each", said: 6, counted: null, rate: 10 },
    ],
    photos: 2,
    picked_up_on: "2026-10-06",
    picked_up_by: "Arjun Pawar",
    drive: diwali,
    status: "to_verify",
    points_awarded: null,
    has_unsuitable: false,
  },
  {
    id: "don-a101",
    flat: "A-101",
    society: "Krishna Kunj",
    lines: [{ key: "books", label: "Books", unit: "each", said: 12, counted: null, rate: 10 }],
    photos: 1,
    picked_up_on: "2026-10-06",
    picked_up_by: "Arjun Pawar",
    drive: diwali,
    status: "to_verify",
    points_awarded: null,
    has_unsuitable: false,
  },
  {
    id: "don-b402",
    flat: "B-402",
    society: "Sunrise Towers",
    lines: [
      { key: "school_bag", label: "School bag", unit: "each", said: 1, counted: 1, rate: 50 },
      { key: "clothes", label: "Clothes", unit: "kg", said: 3, counted: 3, rate: 40 },
    ],
    photos: 2,
    picked_up_on: "2026-09-29",
    picked_up_by: "Arjun Pawar",
    drive: diwali,
    status: "verified",
    points_awarded: 340,
    has_unsuitable: false,
  },
];

const state = () =>
  mockStore("community", () => ({
    drives: /** @type {Drive[]} */ (structuredClone(seedDrives)),
    ngos: /** @type {Ngo[]} */ (structuredClone(seedNgos)),
    donations: /** @type {Donation[]} */ (structuredClone(seedDonations)),
  }));

/** @param {Drive} d @returns {Drive} */
const withState = (d) => ({
  ...d,
  state: d.ends_on < TODAY ? "ended" : d.starts_on > TODAY ? "upcoming" : "running",
});

/** @param {Request} request */
const staffOnly = (request) => {
  const role = roleOf(request);
  return role === "admin" || role === "hub_manager"
    ? null
    : problem(403, "auth.forbidden", "Only admins and hub managers can use this");
};

/**
 * Points for a verified donation: counted quantities times the base rate, times the drive multiplier.
 * The same rule the page previews with; the server's number is the one that is awarded.
 * @param {DonationLine[]} lines
 * @param {number} multiplier
 */
export function pointsFor(lines, multiplier) {
  const base = lines.reduce((sum, l) => sum + Math.round((l.counted ?? 0) * l.rate), 0);
  return Math.round(base * multiplier);
}

export const communityHandlers = [
  http.get("*/v1/drives", ({ request }) => {
    const denied = staffOnly(request);
    if (denied) return denied;
    const all = new URL(request.url).searchParams.get("include_ended") === "true";
    const items = state()
      .drives.map(withState)
      .filter((d) => all || d.state !== "ended");
    return HttpResponse.json({ items, next_cursor: null });
  }),

  http.post("*/v1/admin/drives", ({ request }) => {
    const denied = staffOnly(request);
    if (denied) return denied;
    return idempotent(request, (body) => {
      const name = String(body?.name ?? "").trim();
      const errors = [];
      if (!name) errors.push({ field: "name", detail: "Give the drive a name" });
      if (!body?.starts_on) errors.push({ field: "starts_on", detail: "Pick a start date" });
      if (!body?.ends_on) errors.push({ field: "ends_on", detail: "Pick an end date" });
      if (!(Number(body?.multiplier) >= 1))
        errors.push({ field: "multiplier", detail: "Pick a multiplier" });
      if (errors.length) return problem(422, "request.invalid", "Check the form", { errors });
      if (body.ends_on < body.starts_on)
        return problem(422, "community.drive_dates_invalid", "A drive can't end before it starts");
      /** @type {Drive} */
      const drive = {
        id: `drive-${state().drives.length + 1}`,
        name,
        description: String(body.description ?? "").trim(),
        starts_on: body.starts_on,
        ends_on: body.ends_on,
        multiplier: Number(body.multiplier),
      };
      state().drives.unshift(drive);
      return HttpResponse.json(withState(drive), { status: 201 });
    });
  }),

  http.get("*/v1/admin/point-rates", ({ request }) => {
    const denied = staffOnly(request);
    if (denied) return denied;
    return HttpResponse.json({ items: RATES });
  }),

  http.get("*/v1/ngos", ({ request }) => {
    const denied = staffOnly(request);
    if (denied) return denied;
    return HttpResponse.json({ items: state().ngos, next_cursor: null });
  }),

  http.post("*/v1/admin/ngos", ({ request }) => {
    const denied = staffOnly(request);
    if (denied) return denied;
    return idempotent(request, (body) => {
      const name = String(body?.name ?? "").trim();
      if (!name)
        return problem(422, "request.invalid", "Check the form", {
          errors: [{ field: "name", detail: "Give the NGO a name" }],
        });
      /** @type {Ngo} */
      const ngo = {
        id: `ngo-${state().ngos.length + 1}`,
        name,
        note: null,
        coordinator: String(body?.coordinator ?? "").trim() || null,
        verified: false,
      };
      state().ngos.push(ngo);
      return HttpResponse.json(ngo, { status: 201 });
    });
  }),

  // Donations of the hub, newest pickups first; `status=to_verify` for the queue.
  http.get("*/v1/admin/donations", ({ request }) => {
    const denied = staffOnly(request);
    if (denied) return denied;
    const status = new URL(request.url).searchParams.get("status");
    const items = state().donations.filter((d) => !status || d.status === status);
    return HttpResponse.json({ items, as_of: "2026-10-06T11:10:00+05:30", next_cursor: null });
  }),

  // Verify a donation: the hub's counted quantities decide the points (US-5).
  http.post("*/v1/donations/:id/verify", ({ request, params }) => {
    const denied = staffOnly(request);
    if (denied) return denied;
    return idempotent(request, (body) => {
      const donation = state().donations.find((d) => d.id === params.id);
      if (!donation) return problem(404, "community.donation_not_found", "No such donation");
      if (donation.status === "verified")
        return problem(409, "community.already_verified", "This donation is already verified");
      const counted = body?.counted ?? {};
      const bad = donation.lines.some((l) => !(Number(counted[l.key]) >= 0));
      if (bad) return problem(422, "request.invalid", "Count every item (0 or more)");
      for (const l of donation.lines) l.counted = Number(counted[l.key]);
      donation.has_unsuitable = body?.has_unsuitable === true;
      donation.points_awarded = pointsFor(donation.lines, donation.drive.multiplier);
      donation.status = "verified";
      return HttpResponse.json({ id: donation.id, points_awarded: donation.points_awarded });
    });
  }),
];
