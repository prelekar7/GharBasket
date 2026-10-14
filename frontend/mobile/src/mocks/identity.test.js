import { describe, expect, it } from "vitest";
import { householdBody } from "@/features/onboarding/submit.js";
import { mockFetch } from "./index.js";

/** @param {string} phone */
const signIn = (phone) =>
  mockFetch(
    new Request("http://localhost/v1/auth/exchange", {
      method: "POST",
      headers: { "content-type": "application/json", "x-gb-client": "web" },
      body: JSON.stringify({ dev_phone: phone, device: { platform: "web", push_token: null } }),
    }),
  );

const call = (
  /** @type {string} */ method,
  /** @type {string} */ path,
  /** @type {object | undefined} */ body = undefined,
  /** @type {Record<string, string>} */ headers = {},
) =>
  mockFetch(
    new Request(`http://localhost${path}`, {
      method,
      headers: { "content-type": "application/json", "x-gb-client": "web", ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );

const NEW_PHONE = "+919800000777";
const member = (/** @type {object} */ extra = {}) => ({
  age_band: "18_59",
  fasting_rules: { jain: false, fasts: [], no_onion_garlic_on_fast_days: false },
  health_tags: [],
  ...extra,
});
const body = (/** @type {object} */ extra = {}) => ({
  society_id: "sunrise-towers",
  address: { tower_id: "b", flat: "B-402", location: null },
  diet: "veg",
  cuisine_tags: ["maharashtrian"],
  weekday_cook_minutes: 30,
  weekend_cook_minutes: 60,
  no_cook_days: [],
  budget_paise: 300000,
  members: [member(), member({ age_band: "5_17" })],
  consents: [
    { purpose: "service_delivery", version: "v1", granted: true },
    { purpose: "personalisation", version: "v1", granted: true },
  ],
  ...extra,
});
const create = (/** @type {object} */ b = body(), key = "k1") =>
  call("POST", "/v1/households", b, { "idempotency-key": key });

describe("POST /v1/households (onboarding in one call, M1)", () => {
  it("creates the household with members, address and consents, as HouseholdOut", async () => {
    await signIn(NEW_PHONE);
    expect((await call("GET", "/v1/households/me")).status).toBe(404);
    const res = await create();
    expect(res.status).toBe(201);
    expect(res.headers.get("etag")).toBe('"1"');
    const h = await res.json();
    expect(h).toMatchObject({
      version: 1,
      size: 2,
      diet: "veg",
      budget_paise: 300000,
      autopilot_enabled: true,
      auto_approve: false,
      address: { tower_id: "b", flat: "B-402", household_id: h.id, location: null },
    });
    expect(h.members[0]).toHaveProperty("id");
    expect((await (await call("GET", "/v1/households/me")).json()).id).toBe(h.id);
    const consents = await (await call("GET", "/v1/consents")).json();
    expect(consents).toHaveLength(5);
    expect(
      consents.filter((/** @type {any} */ c) => c.granted).map((/** @type {any} */ c) => c.purpose),
    ).toEqual(["service_delivery", "personalisation"]);
  });

  it("replays the same key, refuses a changed body, and a second household", async () => {
    await signIn(NEW_PHONE);
    const a = await (await create()).json();
    expect((await (await create()).json()).id).toBe(a.id);
    const changed = await create(body({ diet: "egg" }));
    expect(changed.status).toBe(422);
    expect((await changed.json()).code).toBe("request.idempotency_mismatch");
    const again = await create(body(), "k2");
    expect(again.status).toBe(409);
    expect(await again.json()).toMatchObject({
      code: "identity.household_exists",
      household_id: a.id,
    });
  });

  it("answers the contract's errors", async () => {
    await signIn(NEW_PHONE);
    expect((await call("POST", "/v1/households", body())).status).toBe(400);
    const extra = await create({ ...body(), size: 2 });
    expect(await extra.json()).toMatchObject({
      code: "request.invalid",
      errors: [{ loc: ["body", "size"], type: "extra_forbidden" }],
    });
    const noConsent = await create(body({ consents: [] }), "k3");
    expect((await noConsent.json()).code).toBe("identity.consent_required");
    const jain = member({
      fasting_rules: { jain: true, fasts: [], no_onion_garlic_on_fast_days: false },
    });
    const conflict = await create(body({ diet: "egg", members: [jain] }), "k4");
    expect((await conflict.json()).code).toBe("identity.diet_conflict");
    const tags = await create(body({ members: [member({ health_tags: ["diabetes"] })] }), "k5");
    expect(tags.status).toBe(403);
  });

  it("is what the onboarding draft turns into", async () => {
    await signIn(NEW_PHONE);
    const draft = /** @type {any} */ ({
      societyId: "sunrise-towers",
      towerId: "b",
      flat: " 402 ",
      household: {
        members: { adults: 2, seniors: 1, children: 1, little: 0 },
        diet: "veg",
        cuisines: ["gujarati"],
        jain: true,
        noOnionSomeDays: true,
        fasting: ["ekadashi"],
        healthConsent: true,
        health: { "adults-2": ["diabetes"] },
        weekdayCookMinutes: 30,
        weekendCookMinutes: 60,
        noCookDays: [6],
        budgetLockPaise: null,
        whatsappConsent: true,
        insightsConsent: false,
      },
    });
    const b = householdBody(draft);
    expect(b.address).toEqual({ tower_id: "b", flat: "B-402", location: null });
    expect(b.members.map((m) => m.age_band)).toEqual(["18_59", "18_59", "60_plus", "5_17"]);
    // Everyone is Jain; only adults and seniors fast.
    expect(b.members.map((m) => m.fasting_rules.jain)).toEqual([true, true, true, true]);
    expect(b.members.map((m) => m.fasting_rules.fasts.length)).toEqual([1, 1, 1, 0]);
    expect(b.members[1]?.health_tags).toEqual(["diabetes"]);
    expect(b.consents.map((c) => c.purpose)).toEqual([
      "service_delivery",
      "personalisation",
      "health_personalisation",
      "marketing",
    ]);
    expect((await create(b)).status).toBe(201);
  });
});

describe("household edits (M1)", () => {
  it("patches the household's own settings with a quoted If-Match", async () => {
    await signIn("+919800000001");
    const h = await (await call("GET", "/v1/households/me")).json();
    expect(h).toMatchObject({ size: 4, version: 1 });
    const patch = (/** @type {object} */ b, /** @type {Record<string, string>} */ headers) =>
      call("PATCH", `/v1/households/${h.id}`, b, headers);
    expect((await patch({ diet: "egg" }, {})).status).toBe(428);
    expect((await patch({ diet: "egg" }, { "if-match": '"7"' })).status).toBe(412);
    expect((await patch({ members: [] }, { "if-match": '"1"' })).status).toBe(422);
    const ok = await patch({ diet: "egg", budget_paise: null }, { "if-match": '"1"' });
    expect(await ok.json()).toMatchObject({ version: 2, diet: "egg", budget_paise: null });
  });

  it("adds, changes and removes members, and the size follows", async () => {
    await signIn("+919800000001");
    const h = await (await call("GET", "/v1/households/me")).json();
    const base = `/v1/households/${h.id}/members`;
    const added = await call("POST", base, member({ age_band: "under_5" }));
    expect(added.status).toBe(201);
    const m = await added.json();
    const changed = await call("PATCH", `${base}/${m.id}`, {
      fasting_rules: { jain: true, fasts: [], no_onion_garlic_on_fast_days: false },
    });
    expect((await changed.json()).fasting_rules.jain).toBe(true);
    expect((await call("DELETE", `${base}/${m.id}`)).status).toBe(204);
    const now = await (await call("GET", "/v1/households/me")).json();
    expect(now).toMatchObject({ size: 4, version: 4 });
    for (const x of now.members.slice(1)) await call("DELETE", `${base}/${x.id}`);
    const last = await call("DELETE", `${base}/${now.members[0].id}`);
    expect((await last.json()).code).toBe("identity.household_needs_member");
  });

  it("keeps health tags behind the health consent, and erases them when it is revoked", async () => {
    await signIn("+919800000001");
    const h = await (await call("GET", "/v1/households/me")).json();
    expect(h.members.some((/** @type {any} */ x) => x.health_tags.length)).toBe(true);
    const revoked = await call("POST", "/v1/consents", {
      purpose: "health_personalisation",
      version: "v1",
      granted: false,
    });
    expect(await revoked.json()).toMatchObject({ granted: false });
    const now = await (await call("GET", "/v1/households/me")).json();
    expect(now.members.every((/** @type {any} */ x) => x.health_tags.length === 0)).toBe(true);
    const tagged = await call(
      "POST",
      `/v1/households/${h.id}/members`,
      member({ health_tags: ["pcos"] }),
    );
    expect((await tagged.json()).code).toBe("identity.health_consent_required");
    const stale = await call("POST", "/v1/consents", {
      purpose: "marketing",
      version: "v0",
      granted: true,
    });
    expect(await stale.json()).toMatchObject({
      code: "identity.consent_version_stale",
      current_version: "v1",
    });
  });

  it("changes the flat: tower_id and the whole label", async () => {
    await signIn("+919800000001");
    const h = await (await call("GET", "/v1/households/me")).json();
    const res = await call("PATCH", `/v1/addresses/${h.address.id}`, {
      tower_id: "c",
      flat: "c-701",
    });
    expect(await res.json()).toMatchObject({ tower_id: "c", flat: "C-701" });
    expect((await (await call("GET", "/v1/households/me")).json()).address.flat).toBe("C-701");
    expect((await call("PATCH", `/v1/addresses/${h.address.id}`, { tower: "c" })).status).toBe(422);
    expect((await call("PATCH", `/v1/addresses/nope`, { flat: "1" })).status).toBe(404);
  });
});

describe("subscription (M3)", () => {
  it("is created once, wallet only, and paused through /current", async () => {
    await signIn(NEW_PHONE);
    await create();
    const sub = (/** @type {object} */ b, key = "s1") =>
      call("POST", "/v1/subscriptions", b, { "idempotency-key": key });
    expect((await call("GET", "/v1/subscriptions/current")).status).toBe(404);
    const autopay = await sub({ plan: "autopilot", payment_mode: "autopay" });
    expect((await autopay.json()).code).toBe("subscriptions.payment_mode_unavailable");
    const made = await sub({ plan: "autopilot", payment_mode: "wallet" });
    expect(made.status).toBe(201);
    expect(await made.json()).toMatchObject({
      status: "active",
      payment_mode: "wallet",
      paused_until: null,
      auto_approve: false,
    });
    expect(
      (await (await sub({ plan: "autopilot", payment_mode: "wallet" }, "s2")).json()).code,
    ).toBe("subscriptions.exists");
    const paused = await call("PATCH", "/v1/subscriptions/current", { paused_until: "2099-01-01" });
    expect(await paused.json()).toMatchObject({ status: "paused", paused_until: "2099-01-01" });
    const on = await call("PATCH", "/v1/subscriptions/current", { paused_until: null });
    expect(await on.json()).toMatchObject({ status: "active", paused_until: null });
  });
});

describe("me and devices (M1)", () => {
  it("returns MeOut with roles from PATCH /v1/me, and request.invalid for a bad locale", async () => {
    await signIn("+919800000001");
    const ok = await call("PATCH", "/v1/me", { senior_mode: true });
    expect(await ok.json()).toMatchObject({ senior_mode: true, roles: [{ role: "customer" }] });
    const bad = await call("PATCH", "/v1/me", { locale: "fr-FR" });
    expect(await bad.json()).toMatchObject({ code: "request.invalid" });
  });

  it("lists devices as a bare list and signs one out", async () => {
    await signIn("+919800000001");
    const list = await (await call("GET", "/v1/me/devices")).json();
    expect(Array.isArray(list)).toBe(true);
    expect(list[0]).toHaveProperty("signed_in_at");
    const other = list.find((/** @type {any} */ d) => !d.current);
    expect((await call("DELETE", `/v1/me/devices/${other.id}`)).status).toBe(204);
    expect((await call("DELETE", `/v1/me/devices/${other.id}`)).status).toBe(404);
  });
});
