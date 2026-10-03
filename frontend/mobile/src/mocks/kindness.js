// M9 kindness mocks (docs/modules/M9-community.md §3, workflow W8): drives and donations.
// The spec lists endpoints and tables, not JSON; these shapes are our proposal, including
// GET /v1/donations (your history) and POST /v1/files/presign answered by a fake S3. Points come
// only after the hub verifies the items, so scheduling a pickup never changes the balance.
import { HttpResponse, http } from "msw";
import { caller } from "./handlers.js";

const KEY = "gb-mock-kindness";
const ITEMS = ["clothes", "books", "toys", "school_bags", "utensils"];

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code },
    { status, headers: { "content-type": "application/problem+json" } },
  );

/** @returns {{ donations: any[], byKey: Record<string, string> }} */
const load = () => {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (s) return s;
  } catch {
    // fall through to the seed
  }
  return {
    donations: [
      {
        id: "don-1",
        items: [
          { code: "school_bags", qty: 1 },
          { code: "clothes", kg: 3 },
        ],
        photos: [],
        status: "VERIFIED",
        pickup_on: "2026-09-29",
        points_awarded: 340,
      },
    ],
    byKey: {},
  };
};
const save = (/** @type {ReturnType<typeof load>} */ s) =>
  localStorage.setItem(KEY, JSON.stringify(s));

export const kindnessHandlers = [
  http.get("*/v1/drives", () =>
    HttpResponse.json({
      items: [
        {
          id: "drv-1",
          code: "diwali",
          starts_on: "2026-10-01",
          ends_on: "2026-10-31",
          multiplier: 2,
        },
      ],
    }),
  ),

  // Pickups ride the van's return trip after your next delivery (W8): Tuesday 13 Oct here.
  http.get("*/v1/donations", ({ request }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    const s = load();
    return HttpResponse.json({
      items: [...s.donations].reverse(),
      next_cursor: null,
      next_pickup_on: "2026-10-13",
    });
  }),

  http.post("*/v1/donations", async ({ request }) => {
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(
        400,
        "platform.idempotency_key_required",
        "Idempotency-Key header is required",
      );
    const s = load();
    const replay = s.donations.find((d) => d.id === s.byKey[key]);
    if (replay) return HttpResponse.json(replay, { status: 201 });
    const body = /** @type {{ items?: string[], photos?: string[], pickup_on?: string }} */ (
      await request.json()
    );
    const items = (body.items ?? []).filter((c) => ITEMS.includes(c));
    if (items.length === 0) return problem(422, "community.no_items", "Pick at least one thing");
    const donation = {
      id: `don-${s.donations.length + 1}`,
      items: items.map((code) => ({ code })),
      photos: body.photos ?? [],
      status: "SCHEDULED",
      pickup_on: body.pickup_on ?? "2026-10-13",
      points_awarded: null,
    };
    s.donations.push(donation);
    s.byKey[key] = donation.id;
    save(s);
    return HttpResponse.json(donation, { status: 201 });
  }),

  // Direct-to-storage upload (handbook §10.1): presign, PUT the bytes, keep only the key.
  http.post("*/v1/files/presign", async ({ request }) => {
    const { content_type: type = "image/jpeg" } = /** @type {{ content_type?: string }} */ (
      await request.json()
    );
    const key = `donations/${crypto.randomUUID()}.${type.split("/")[1] ?? "bin"}`;
    return HttpResponse.json({ key, url: `${location.origin}/mock-s3/${key}`, method: "PUT" });
  }),
  http.put("*/mock-s3/*", () => new HttpResponse(null, { status: 200 })),
];
