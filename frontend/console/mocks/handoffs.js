// NGO hand-off mocks (M9 section 3.5): the surplus offers NGOs claim, what is ready to hand over, and the
// hand-offs the hub or an NGO coordinator records with photo proof and the coordinator confirms. Seeded story:
// October 2026, Kharghar hub, one verified donation and one claimed offer waiting, six donated meals.
//
// Not in the spec (kept here, listed as a backend ask): the shape of `ready.donations[].items`; here it is the
// donation's items as `{ code, unit, counted }`.
import { HttpResponse, http } from "msw";
import { communityStore, hubDenied, NOW, need, page, photoUrls } from "./community.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { NGO_ID } from "./ids.js";
import { fieldError, idempotent, mockStore, mockUuid, problem, roleOf } from "./util.js";

/**
 * @typedef {{ id: string, name: string }} NgoCard
 * @typedef {{ code: string, unit: "kg" | "each", qty: number, counted: number | null, points: number | null }} DonationItem
 * @typedef {{ offer_id: string, hub_id: string, product: { id: string, name: string }, qty: number, unit: string,
 *   kg: number, expires_at: string, status: "open" | "claimed" | "expired", claimed_by: NgoCard | null }} Offer
 * @typedef {{ id: string, ngo: NgoCard, hub_id: string, kind: "items" | "food" | "meals", donation_ids: string[],
 *   offer_ids: string[], meals: number, summary: { items: number, kg: number, meals: number }, photos: number,
 *   people_reached: number | null, handed_at: string, recorded_by: "hub" | "ngo", confirmed_at: string | null,
 *   note: string | null }} Handoff
 */

const NGO1 = { id: NGO_ID.first, name: "Partner NGO 1" };

/** @returns {Offer[]} */
const seedOffers = () => [
  {
    offer_id: mockUuid("bb00", 1),
    hub_id: KHARGHAR_HUB_ID,
    product: { id: mockUuid("b000", 7), name: "Spinach (palak)" },
    qty: 3,
    unit: "kg",
    kg: 3,
    expires_at: "2026-10-06T14:00:00+05:30",
    status: "open",
    claimed_by: null,
  },
  {
    offer_id: mockUuid("bb00", 2),
    hub_id: KHARGHAR_HUB_ID,
    product: { id: mockUuid("b000", 9), name: "Bhindi (okra)" },
    qty: 1.5,
    unit: "kg",
    kg: 1.5,
    expires_at: "2026-10-06T14:00:00+05:30",
    status: "claimed",
    claimed_by: NGO1,
  },
];

/** @returns {Handoff[]} */
const seedHandoffs = () => [
  {
    id: mockUuid("bc00", 1),
    ngo: NGO1,
    hub_id: KHARGHAR_HUB_ID,
    kind: "items",
    donation_ids: [mockUuid("ba00", 90)],
    offer_ids: [],
    meals: 0,
    summary: { items: 4, kg: 3, meals: 0 },
    photos: 2,
    people_reached: 24,
    handed_at: "2026-10-01T11:30:00+05:30",
    recorded_by: "hub",
    confirmed_at: "2026-10-01T15:00:00+05:30",
    note: "Class 5, municipal school, Sector 12",
  },
  {
    id: mockUuid("bc00", 2),
    ngo: NGO1,
    hub_id: KHARGHAR_HUB_ID,
    kind: "food",
    donation_ids: [],
    offer_ids: [mockUuid("bb00", 90)],
    meals: 0,
    summary: { items: 0, kg: 1.5, meals: 0 },
    photos: 1,
    people_reached: 40,
    handed_at: "2026-10-06T10:20:00+05:30",
    recorded_by: "hub",
    confirmed_at: null,
    note: "Community kitchen, Sector 8",
  },
];

export const handoffsStore = () =>
  mockStore("handoffs", () => ({
    offers: seedOffers(),
    handoffs: seedHandoffs(),
    mealsWaiting: 6,
  }));

/** The hand-off state with the donations beside it, which hand-offs mark as handed over. */
function state() {
  const donations = communityStore();
  const h = handoffsStore();
  return {
    donations: donations.donations,
    ngos: donations.ngos,
    offers: h.offers,
    handoffs: h.handoffs,
    get mealsWaiting() {
      return h.mealsWaiting;
    },
    set mealsWaiting(n) {
      h.mealsWaiting = n;
    },
  };
}

/** @param {Handoff} h */
const handoffOut = (h) => ({ ...h, photo_urls: photoUrls(h.photos) });

/** The month of an ISO timestamp, "2026-10". @param {string} iso */
export const monthOf = (iso) => iso.slice(0, 7);

export const handoffsHandlers = [
  // GET /v1/ngo/offers?hub_id=&status=open : soonest expiry first
  http.get("*/v1/ngo/offers", ({ request }) => {
    const denied = need(request, ["admin", "hub_manager", "ngo_coordinator"]);
    if (denied) return denied;
    const q = new URL(request.url).searchParams;
    const bad = hubDenied(request, q.get("hub_id"));
    if (bad) return bad;
    const status = q.get("status");
    return page(
      state()
        .offers.filter((o) => !status || o.status === status)
        .sort((a, b) => a.expires_at.localeCompare(b.expires_at)),
    );
  }),

  // POST /v1/ngo/offers/{offer_id}/claim : the first claim wins
  http.post("*/v1/ngo/offers/:offerId/claim", ({ request, params }) => {
    const denied = need(request, ["ngo_coordinator"]);
    if (denied) return denied;
    return idempotent(request, (body) => {
      const s = state();
      const offer = s.offers.find((o) => o.offer_id === params.offerId);
      if (!offer) return problem(404, "community.offer_not_found", "No such offer");
      const ngo = s.ngos.find((n) => n.id === body?.ngo_id);
      if (!ngo || ngo.id !== NGO_ID.first)
        return problem(403, "auth.forbidden", "That is not your NGO");
      if (offer.status === "expired" || offer.expires_at < NOW)
        return problem(409, "community.offer_expired", "This offer has expired");
      if (offer.status === "claimed")
        return problem(409, "community.offer_taken", "Another NGO claimed it first");
      offer.status = "claimed";
      offer.claimed_by = { id: ngo.id, name: ngo.name };
      return HttpResponse.json(offer);
    });
  }),

  // GET /v1/ngo-handoffs/ready?hub_id= : verified donations, claimed offers and donated meals not handed over yet
  http.get("*/v1/ngo-handoffs/ready", ({ request }) => {
    const denied = need(request, ["admin", "hub_manager", "ngo_coordinator"]);
    if (denied) return denied;
    const bad = hubDenied(request, new URL(request.url).searchParams.get("hub_id"));
    if (bad) return bad;
    const s = state();
    const handed = new Set(s.handoffs.flatMap((h) => h.donation_ids));
    const offersHanded = new Set(s.handoffs.flatMap((h) => h.offer_ids));
    return HttpResponse.json({
      donations: s.donations
        .filter((d) => d.status === "verified" && !handed.has(d.id))
        .map((d) => ({
          id: d.id,
          items: d.items.map((i) => ({ code: i.code, unit: i.unit, counted: i.counted })),
          verified_on:
            d.timeline.find((t) => t.status === "verified")?.at.slice(0, 10) ?? d.pickup_on,
        })),
      offers: s.offers.filter((o) => o.status === "claimed" && !offersHanded.has(o.offer_id)),
      meals_waiting: s.mealsWaiting,
    });
  }),

  // GET /v1/ngo-handoffs?hub_id=&ngo_id=&month= : a page of hand-offs and the month's report
  http.get("*/v1/ngo-handoffs", ({ request }) => {
    const denied = need(request, ["admin", "hub_manager", "ngo_coordinator"]);
    if (denied) return denied;
    const q = new URL(request.url).searchParams;
    const bad = hubDenied(request, q.get("hub_id"));
    if (bad) return bad;
    const ngoId = roleOf(request) === "ngo_coordinator" ? NGO_ID.first : q.get("ngo_id");
    const month = q.get("month") ?? monthOf(NOW);
    if (!/^\d{4}-\d{2}$/.test(month))
      return problem(422, "request.invalid", "month is YYYY-MM", {
        errors: [
          {
            loc: ["query", "month"],
            message: "Input should be a month like 2026-10",
            type: "value_error",
          },
        ],
      });
    const items = state()
      .handoffs.filter((h) => monthOf(h.handed_at) === month && (!ngoId || h.ngo.id === ngoId))
      .sort((a, b) => a.handed_at.localeCompare(b.handed_at));
    const sum = (/** @type {(h: Handoff) => number} */ f) => items.reduce((n, h) => n + f(h), 0);
    return HttpResponse.json({
      items: items.map(handoffOut),
      next_cursor: null,
      report: {
        handoffs: items.length,
        items: sum((h) => h.summary.items),
        kg: sum((h) => h.summary.kg),
        meals: sum((h) => h.summary.meals),
        people_reached: sum((h) => h.people_reached ?? 0),
      },
    });
  }),

  // POST /v1/ngo-handoffs : the hub or the NGO's coordinator records a gift, with 1 to 3 photo keys
  http.post("*/v1/ngo-handoffs", ({ request }) => {
    const denied = need(request, ["hub_manager", "ngo_coordinator"]);
    if (denied) return denied;
    return idempotent(request, (body) => {
      const s = state();
      const ngo = s.ngos.find((n) => n.id === body?.ngo_id);
      if (!ngo) return problem(404, "community.ngo_not_found", "No such NGO");
      const role = roleOf(request);
      if (role === "ngo_coordinator" && ngo.id !== NGO_ID.first)
        return problem(403, "auth.forbidden", "That is not your NGO");
      const bad = hubDenied(request, body?.hub_id ?? null);
      if (bad) return bad;
      const kind = body?.kind;
      if (!["items", "food", "meals"].includes(kind))
        return problem(422, "request.invalid", "Pick what is being handed over", {
          errors: [fieldError("kind", "Pick items, food or meals")],
        });
      if (
        !ngo.verified ||
        ngo.status !== "active" ||
        !ngo.accepts.includes(kind) ||
        !ngo.hub_ids.includes(body.hub_id)
      )
        return problem(422, "community.ngo_does_not_accept", "This NGO cannot take that here");
      const keys = Array.isArray(body.photo_keys) ? body.photo_keys : [];
      if (
        keys.length < 1 ||
        keys.length > 3 ||
        keys.some((/** @type {string} */ k) => !k.startsWith("handoff_photo/"))
      )
        return problem(422, "community.photo_key_invalid", "Add 1 to 3 photos of the hand-over");

      const donationIds = body.donation_ids ?? [];
      const offerIds = body.offer_ids ?? [];
      const meals = Number(body.meals ?? 0);
      const wrongKind =
        (kind === "items" && (offerIds.length > 0 || meals > 0)) ||
        (kind === "food" && (donationIds.length > 0 || meals > 0)) ||
        (kind === "meals" && (donationIds.length > 0 || offerIds.length > 0));
      const empty =
        (kind === "items" && donationIds.length === 0) ||
        (kind === "food" && offerIds.length === 0) ||
        (kind === "meals" && !(meals > 0));
      if (wrongKind || empty)
        return problem(422, "community.handoff_empty", "Nothing to hand over for this kind");

      const handed = new Set(s.handoffs.flatMap((h) => h.donation_ids));
      const notReady = donationIds.filter((/** @type {string} */ id) => {
        const d = s.donations.find((x) => x.id === id);
        return d?.status !== "verified" || handed.has(id);
      });
      if (notReady.length)
        return problem(
          409,
          "community.donation_not_ready",
          "Some donations are not ready to hand over",
          { donation_ids: notReady },
        );
      const offers = offerIds.map((/** @type {string} */ id) =>
        s.offers.find((o) => o.offer_id === id),
      );
      if (offers.some((/** @type {Offer | undefined} */ o) => !o || o.claimed_by?.id !== ngo.id))
        return problem(404, "community.offer_not_found", "That offer is not one this NGO claimed");
      if (kind === "meals" && meals > s.mealsWaiting)
        return problem(422, "community.meals_exceed_pledged", "More meals than are waiting", {
          meals_waiting: s.mealsWaiting,
        });

      /** @type {DonationItem[]} */
      const lines = donationIds.flatMap(
        (/** @type {string} */ id) => s.donations.find((x) => x.id === id)?.items ?? [],
      );
      const kg =
        kind === "food"
          ? offers.reduce((/** @type {number} */ n, /** @type {Offer} */ o) => n + o.kg, 0)
          : lines.filter((i) => i.unit === "kg").reduce((n, i) => n + (i.counted ?? 0), 0);
      /** @type {Handoff} */
      const handoff = {
        id: mockUuid("bc00", s.handoffs.length + 1),
        ngo: { id: ngo.id, name: ngo.name },
        hub_id: body.hub_id,
        kind,
        donation_ids: donationIds,
        offer_ids: offerIds,
        meals: kind === "meals" ? meals : 0,
        summary: {
          items: lines.filter((i) => i.unit === "each").reduce((n, i) => n + (i.counted ?? 0), 0),
          kg,
          meals: kind === "meals" ? meals : 0,
        },
        photos: keys.length,
        people_reached: body.people_reached ?? null,
        handed_at: body.handed_at ?? NOW,
        recorded_by: role === "ngo_coordinator" ? "ngo" : "hub",
        confirmed_at: role === "ngo_coordinator" ? NOW : null,
        note: body.note ?? null,
      };
      for (const id of donationIds) {
        const d = s.donations.find((x) => x.id === id);
        if (d) {
          d.status = "handed_over";
          d.ngo = { name: ngo.name };
        }
      }
      if (kind === "meals") s.mealsWaiting -= meals;
      s.handoffs.push(handoff);
      return HttpResponse.json(handoffOut(handoff), { status: 201 });
    });
  }),

  // POST /v1/ngo-handoffs/{id}/confirm : the NGO's coordinator confirms receipt
  http.post("*/v1/ngo-handoffs/:id/confirm", ({ request, params }) => {
    const denied = need(request, ["ngo_coordinator"]);
    if (denied) return denied;
    return idempotent(request, (body) => {
      const h = state().handoffs.find((x) => x.id === params.id && x.ngo.id === NGO_ID.first);
      if (!h) return problem(404, "community.handoff_not_found", "No such hand-off");
      if (h.confirmed_at)
        return problem(409, "community.handoff_already_confirmed", "Already confirmed");
      h.confirmed_at = NOW;
      if (Number.isInteger(body?.people_reached)) h.people_reached = body.people_reached;
      return HttpResponse.json(handoffOut(h));
    });
  }),
];
