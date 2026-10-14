// M9 kindness mocks (docs/modules/M9-community.md §3.4, §3.7; M0 §3.1 presign): point rates, drives,
// donations and impact in the frozen shapes. Points come only after the hub has counted the items
// (verification), so scheduling a pickup never changes the balance. Donations picked up and
// counted are verified by the hub in the console; here the seed has one verified donation.
import { HttpResponse, http } from "msw";
import { caller, isNewUser } from "./handlers.js";
import { uid } from "./subscriptions.js";

const KEY = "gb-mock-donations";
const POINTS_KEY = "gb-mock-points"; // the Kindness ledger of mocks/billing.js
const IST = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" });
const today = () => IST.format(new Date());
const uuid = () => crypto.randomUUID();

const problem = (
  /** @type {number} */ status,
  /** @type {string} */ code,
  /** @type {string} */ title,
  /** @type {Record<string, unknown>} */ extra = {},
) =>
  HttpResponse.json(
    { type: "about:blank", title, status, code, ...extra },
    { status, headers: { "content-type": "application/problem+json" } },
  );
/** Index of the request language in the [en, hi, mr] triples below. @param {Request} r */
const langOf = (r) => {
  const l = r.headers.get("accept-language") ?? "";
  return l.startsWith("hi") ? 1 : l.startsWith("mr") ? 2 : 0;
};

/** The seed point rates (M9 §3.4); the console may change them. label: [en, hi, mr]. */
const RATES = [
  { code: "clothes", label: ["Clothes", "कपड़े", "कपडे"], unit: "kg", points: 40, max_qty: 15 },
  { code: "utensils", label: ["Utensils", "बर्तन", "भांडी"], unit: "kg", points: 30, max_qty: 10 },
  {
    code: "school_bag",
    label: ["School bags", "स्कूल बैग", "शाळेच्या बॅगा"],
    unit: "each",
    points: 50,
    max_qty: 5,
  },
  { code: "books", label: ["Books", "किताबें", "पुस्तके"], unit: "each", points: 10, max_qty: 30 },
  { code: "toys", label: ["Toys", "खिलौने", "खेळणी"], unit: "each", points: 15, max_qty: 10 },
];

const DIWALI = {
  id: "0199a000-0000-7000-8000-00000d1a0001",
  code: "diwali-2026",
  name: ["Diwali drive", "दिवाली ड्राइव", "दिवाळी ड्राइव्ह"],
  description: [
    "Clothes and toys count double until the end of October.",
    "अक्टूबर के अंत तक कपड़े और खिलौने दोगुने गिने जाएँगे।",
    "ऑक्टोबरच्या अखेरपर्यंत कपडे आणि खेळणी दुप्पट मोजली जातील.",
  ],
  codes: ["clothes", "toys"],
  multiplier: 2.0,
  starts_on: "2026-10-01",
  ends_on: "2026-10-31",
};

/** @param {typeof DIWALI} d @param {number} i @param {any[]} donations */
function driveOut(d, i, donations) {
  const day = today();
  const mine = donations.filter((x) => x.drive?.id === d.id);
  const count = (/** @type {string[]} */ statuses) =>
    mine
      .filter((x) => statuses.includes(x.status))
      .reduce(
        (/** @type {number} */ n, /** @type {any} */ x) =>
          n +
          x.items.reduce(
            (/** @type {number} */ m, /** @type {any} */ it) => m + (it.counted ?? it.qty),
            0,
          ),
        0,
      );
  return {
    id: d.id,
    code: d.code,
    name: d.name[i],
    description: d.description[i],
    scope: "platform",
    hub_id: null,
    society_id: null,
    codes: d.codes,
    multiplier: d.multiplier,
    starts_on: d.starts_on,
    ends_on: d.ends_on,
    state: day < d.starts_on ? "upcoming" : day > d.ends_on ? "ended" : "running",
    ngo: { id: "0199a000-0000-7000-8000-00000000ac01", name: "Seva Sahayog Trust" },
    goal_items: 200,
    progress: {
      pledged: count(["scheduled", "picked_up"]) + 40,
      verified: count(["verified"]) + 31,
    },
  };
}

/** The next two delivery dates (Tuesdays here) after today: open until 22:00 the day before. */
function pickupDates() {
  const out = [];
  const d = new Date(`${today()}T00:00:00Z`);
  while (out.length < 2) {
    d.setUTCDate(d.getUTCDate() + 1);
    if (d.getUTCDay() === 2) out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

/** round-half-up of Σ qty × rate × (drive multiplier on the drive's codes). */
const points = (
  /** @type {{ code: string, qty: number }[]} */ items,
  /** @type {{ codes: string[], multiplier: number } | null} */ drive,
) =>
  Math.floor(
    items.reduce((t, { code, qty }) => {
      const rate = RATES.find((r) => r.code === code)?.points ?? 0;
      return t + qty * rate * (drive?.codes.includes(code) ? drive.multiplier : 1);
    }, 0) + 0.5,
  );

/** @returns {{ donations: any[], byKey: Record<string, string> }} */
const load = () => {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (s) return s;
  } catch {
    // fall through to the seed
  }
  const line = (
    /** @type {string} */ code,
    /** @type {number} */ qty,
    /** @type {number} */ pts,
  ) => ({
    code,
    unit: RATES.find((r) => r.code === code)?.unit,
    qty,
    counted: qty,
    points: pts,
  });
  return {
    donations: [
      {
        id: "0199a000-0000-7000-8000-0000000d0001",
        status: "verified",
        items: [line("school_bag", 1, 50), line("clothes", 3, 120), line("books", 5, 50)],
        photos: 2,
        drive: null,
        pickup_on: "2026-09-29",
        estimated_points: 220,
        points_awarded: 220,
        reject_reason: null,
        cancel_reason: null,
        ngo: null,
        timeline: [
          { status: "scheduled", at: "2026-09-25T06:00:00Z" },
          { status: "picked_up", at: "2026-09-29T07:30:00Z" },
          { status: "verified", at: "2026-09-29T11:00:00Z" },
        ],
        created_at: "2026-09-25T06:00:00Z",
      },
    ],
    byKey: {},
  };
};
const save = (/** @type {ReturnType<typeof load>} */ s) =>
  localStorage.setItem(KEY, JSON.stringify(s));

/** The verified donations, for the impact numbers. */
const verified = () => load().donations.filter((d) => d.status === "verified");

/**
 * GET /v1/impact?scope=me (M9 §3.7), all-time, from the verified donations and the Kindness ledger.
 * The route itself is in mocks/community.js beside the society scope.
 * @param {string} phone
 */
export function myImpact(phone) {
  const none = {
    scope: "me",
    period: "all",
    items_donated: 0,
    kg_donated: 0,
    donations_verified: 0,
    points_earned: 0,
    meals_donated: 0,
    homes_invited: 0,
    donated: [],
  };
  if (isNewUser(phone)) return none;
  /** @type {Record<string, { code: string, unit: string, qty: number }>} */
  const totals = {};
  for (const d of verified())
    for (const it of d.items) {
      totals[it.code] ??= { code: it.code, unit: it.unit, qty: 0 };
      totals[it.code].qty += it.counted ?? 0;
    }
  const donated = Object.values(totals).filter((t) => t.qty > 0);
  const points = JSON.parse(localStorage.getItem(POINTS_KEY) ?? "null");
  /** @type {{ kind: string, points: number }[]} */
  const entries = points?.entries ?? [];
  const sum = (/** @type {string} */ kind) =>
    entries.filter((e) => e.kind === kind).reduce((n, e) => n + e.points, 0);
  return {
    ...none,
    items_donated: donated.reduce((n, t) => n + t.qty, 0),
    kg_donated: donated.filter((t) => t.unit === "kg").reduce((n, t) => n + t.qty, 0),
    donations_verified: verified().length,
    // Without a ledger yet, the demo household has earned its seed points.
    points_earned: points ? sum("points_earned") : 340,
    meals_donated: -sum("points_donated") / 500,
    homes_invited: 2,
    donated,
  };
}

export const kindnessHandlers = [
  // GET /v1/point-rates: points per kg or per piece, labels in the request language.
  http.get("*/v1/point-rates", ({ request }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    const i = langOf(request);
    return HttpResponse.json({
      items: RATES.map(({ label, ...r }) => ({ ...r, label: label[i] })),
    });
  }),

  // GET /v1/drives?state=: running and upcoming by default; name and description in the request language.
  http.get("*/v1/drives", ({ request }) => {
    const state = new URL(request.url).searchParams.get("state");
    const d = driveOut(DIWALI, langOf(request), load().donations);
    const wanted = state ? [state] : ["running", "upcoming"];
    return HttpResponse.json({ items: wanted.includes(d.state) ? [d] : [], next_cursor: null });
  }),

  // GET /v1/donations: your donations newest first, and the next two pickup dates.
  http.get("*/v1/donations", ({ request }) => {
    if (!caller(request)) return problem(401, "auth.invalid_token", "Sign in again");
    return HttpResponse.json({
      items: [...load().donations].reverse(),
      next_cursor: null,
      pickup_dates: pickupDates(),
    });
  }),

  http.post("*/v1/donations", async ({ request }) => {
    const key = request.headers.get("idempotency-key");
    if (!key)
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const s = load();
    const replay = s.donations.find((d) => d.id === s.byKey[key]);
    if (replay) return HttpResponse.json(replay, { status: 201 });
    const body = /** @type {any} */ (await request.json().catch(() => null)) ?? {};
    const lines = Array.isArray(body.items) ? body.items : [];
    if (lines.length === 0) return problem(422, "community.no_items", "Pick at least one thing");
    if (
      lines.length > 5 ||
      new Set(lines.map((/** @type {any} */ l) => l.code)).size !== lines.length
    )
      return problem(422, "request.invalid", "Unprocessable Content");
    for (const { code, qty } of lines) {
      const rate = RATES.find((r) => r.code === code);
      if (!rate) return problem(422, "community.item_code_unknown", "We don't take that yet");
      const step = rate.unit === "kg" ? 0.5 : 1;
      if (typeof qty !== "number" || qty <= 0 || qty > rate.max_qty || (qty / step) % 1 !== 0)
        return problem(422, "community.donation_qty_invalid", "Check how many");
    }
    const dates = pickupDates();
    if (!dates.includes(body.pickup_on))
      return problem(422, "community.pickup_date_invalid", "Pick one of the pickup days", {
        dates,
      });
    const keys = Array.isArray(body.photo_keys) ? body.photo_keys : [];
    if (keys.length > 3 || keys.some((/** @type {string} */ k) => !k.startsWith("donation_photo/")))
      return problem(422, "community.photo_key_invalid", "Use the photos you just added");
    if (s.donations.filter((d) => ["scheduled", "picked_up"].includes(d.status)).length >= 3)
      return problem(409, "community.too_many_open_donations", "Three pickups are already booked");
    /** @type {{ id: string, name: string, multiplier: number, codes: string[] } | null} */
    let drive = null;
    if (body.drive_id) {
      const d = driveOut(DIWALI, 0, s.donations);
      if (body.drive_id !== d.id || d.state !== "running" || body.pickup_on > d.ends_on)
        return problem(422, "community.drive_not_open", "That drive is not open on that day");
      drive = { id: d.id, name: d.name, multiplier: d.multiplier, codes: d.codes };
    }
    const now = new Date().toISOString();
    const donation = {
      id: uuid(),
      status: "scheduled",
      items: lines.map((/** @type {{ code: string, qty: number }} */ l) => ({
        code: l.code,
        unit: RATES.find((r) => r.code === l.code)?.unit,
        qty: l.qty,
        counted: null,
        points: null,
      })),
      photos: keys.length,
      drive: drive && { id: drive.id, name: drive.name, multiplier: drive.multiplier },
      pickup_on: body.pickup_on,
      estimated_points: points(lines, drive),
      points_awarded: null,
      reject_reason: null,
      cancel_reason: null,
      ngo: null,
      timeline: [{ status: "scheduled", at: now }],
      created_at: now,
    };
    s.donations.push(donation);
    s.byKey[key] = donation.id;
    save(s);
    return HttpResponse.json(donation, { status: 201 });
  }),

  // POST /v1/donations/{id}/cancel: only while scheduled.
  http.post("*/v1/donations/:id/cancel", ({ request, params }) => {
    if (!request.headers.get("idempotency-key"))
      return problem(400, "request.idempotency_key_required", "Idempotency-Key header is required");
    const s = load();
    const d = s.donations.find((x) => x.id === params.id);
    if (!d) return problem(404, "community.donation_not_found", "Donation not found");
    if (d.status === "scheduled") {
      d.status = "cancelled";
      d.cancel_reason = "customer";
      d.timeline.push({ status: "cancelled", at: new Date().toISOString() });
      save(s);
    } else if (d.status !== "cancelled")
      return problem(409, "community.donation_not_cancellable", "This pickup can't be cancelled");
    return HttpResponse.json(d);
  }),

  // POST /v1/files/presign (M0 §3.1): a presigned PUT for one private upload. Keys are
  // <purpose>/<user_id>/<uuid>.<ext>; the bytes go to the fake storage below and are never kept.
  http.post("*/v1/files/presign", async ({ request }) => {
    const phone = caller(request);
    if (!phone) return problem(401, "auth.invalid_token", "Sign in again");
    const body = /** @type {any} */ (await request.json().catch(() => null)) ?? {};
    const photo = ["image/jpeg", "image/png", "image/webp"];
    const rules = /** @type {Record<string, { types: string[], max: number }>} */ ({
      order_issue_photo: { types: photo, max: 5_000_000 },
      donation_photo: { types: photo, max: 5_000_000 },
      delivery_photo: { types: photo, max: 5_000_000 }, // M7 names it; M0 §3.1 does not list it yet
      kyc_document: { types: [...photo.slice(0, 2), "application/pdf"], max: 10_000_000 },
    });
    const rule = rules[body.purpose];
    if (!rule) return problem(422, "platform.upload_purpose_invalid", "Unknown upload purpose");
    if (!rule.types.includes(body.content_type))
      return problem(422, "platform.upload_type_not_allowed", "That file type is not allowed", {
        allowed: rule.types,
      });
    if (!Number.isInteger(body.size_bytes) || body.size_bytes < 1 || body.size_bytes > rule.max)
      return problem(422, "platform.upload_too_large", "That file is too large", {
        max_bytes: rule.max,
      });
    const ext =
      { "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp" }[
        /** @type {"image/jpeg"} */ (body.content_type)
      ] ?? "pdf";
    const key = `${body.purpose}/${uid(`user:${phone}`)}/${uuid()}.${ext}`;
    return HttpResponse.json({
      key,
      url: `${location.origin}/mock-s3/${key}`,
      method: "PUT",
      headers: {
        "Content-Type": body.content_type,
        "Content-Length": String(body.size_bytes),
      },
      expires_at: new Date(Date.now() + 10 * 60_000).toISOString(),
    });
  }),
  http.put("*/mock-s3/*", () => new HttpResponse(null, { status: 200 })),
];
