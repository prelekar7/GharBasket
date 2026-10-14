import { afterEach, describe, expect, it, vi } from "vitest";
import { isoWeek } from "@/features/partners/earnings.js";
import { mockFetch } from "./index.js";
import { MODULES, REQUIRED } from "./trainingModules.js";

const call = async (
  /** @type {string} */ method,
  /** @type {string} */ path,
  /** @type {object | undefined} */ body = undefined,
  /** @type {Record<string, string>} */ headers = {},
) =>
  mockFetch(
    new Request(`http://localhost${path}`, {
      method,
      headers: { "content-type": "application/json", "x-gb-client": "web", ...headers },
      body: body ? JSON.stringify(body) : undefined,
    }),
  );
const key = () => ({ "idempotency-key": crypto.randomUUID() });
const signIn = (phone = "+919800000001") => call("POST", "/v1/auth/exchange", { dev_phone: phone });
const photo = (/** @type {string} */ purpose = "kyc_document") => ({
  purpose,
  content_type: "image/jpeg",
  size_bytes: 1000,
});
/** A kyc_document upload, as the app does it before sending the key. */
const upload = async () => (await (await call("POST", "/v1/files/presign", photo())).json()).key;

/** Applies for a kind and returns the application. @param {string} kind */
const apply = async (kind) =>
  (await call("POST", "/v1/partner-applications", { kind }, key())).json();
const send = (/** @type {object} */ body, /** @type {Record<string, string>} */ headers = key()) =>
  call("POST", "/v1/kyc-documents", body, headers);

describe("partner applications (M8 §3.1)", () => {
  afterEach(() => vi.useRealTimers());

  it("answers 404 until you apply, then creates one application per key", async () => {
    await signIn();
    const none = await call("GET", "/v1/partner-applications/me");
    expect(none.status).toBe(404);
    expect((await none.json()).code).toBe("partners.application_not_found");
    expect((await call("POST", "/v1/partner-applications", { kind: "kitchen" })).status).toBe(400);
    const bad = await call("POST", "/v1/partner-applications", { kind: "cook" }, key());
    expect((await bad.json()).code).toBe("partners.kind_invalid");
    const k = key();
    const res = await call("POST", "/v1/partner-applications", { kind: "kitchen" }, k);
    expect(res.status).toBe(201);
    const app = await res.json();
    expect(app).toMatchObject({
      kind: "kitchen",
      status: "applied",
      scope_type: "hub",
      display_name: "Priya",
      partner_id: null,
      reason: null,
      next_step: "documents",
      requirements: {
        documents: ["id_proof", "pan", "bank", "fssai", "kitchen_photos"].map((doc_type) => ({
          doc_type,
          status: "missing",
          document_id: null,
          reason: null,
        })),
        trainings: [
          { code: "app_basics", status: "todo" },
          { code: "hygiene", status: "todo" },
          { code: "kitchen_ops", status: "todo" },
        ],
      },
    });
    expect(
      (await (await call("POST", "/v1/partner-applications", { kind: "kitchen" }, k)).json()).id,
    ).toBe(app.id);
    const again = await call("POST", "/v1/partner-applications", { kind: "rider" }, key());
    expect(again.status).toBe(409);
    expect(await again.json()).toMatchObject({
      code: "partners.already_applied",
      application_id: app.id,
    });
    expect((await (await call("GET", "/v1/partner-applications/me")).json()).id).toBe(app.id);
    expect((await call("GET", "/v1/partner-applications/nope")).status).toBe(404);
  });

  it("needs a household, and a captain applies only for her own society", async () => {
    await signIn("+919800000009");
    const no = await call("POST", "/v1/partner-applications", { kind: "rider" }, key());
    expect((await no.json()).code).toBe("partners.household_required");
    await signIn();
    const away = await call(
      "POST",
      "/v1/partner-applications",
      { kind: "captain", scope_id: "0199a000-0000-7000-8000-00000000b002" },
      key(),
    );
    expect((await away.json()).code).toBe("partners.captain_not_resident");
    const hub = await call(
      "POST",
      "/v1/partner-applications",
      { kind: "packer", scope_id: "x" },
      key(),
    );
    expect((await hub.json()).code).toBe("partners.scope_invalid");
  });

  it("withdraws an open application, and then you can apply again", async () => {
    await signIn();
    const app = await apply("packer");
    const res = await call("POST", `/v1/partner-applications/${app.id}/withdraw`, undefined, key());
    expect(await res.json()).toMatchObject({ status: "withdrawn", next_step: null });
    expect(
      (await call("POST", `/v1/partner-applications/${app.id}/withdraw`, undefined, key())).status,
    ).toBe(409);
    expect((await apply("packer")).status).toBe("applied");
  });

  it("checks documents without ever keeping a full number, never Aadhaar", async () => {
    await signIn();
    const app = await apply("kitchen");
    const file = await upload();
    expect(file).toMatch(/^kyc_document\/[0-9a-f-]+\/[0-9a-f-]+\.jpg$/);
    const code = async (/** @type {object} */ body) => {
      const res = await send({ application_id: app.id, ...body });
      return [res.status, (await res.json()).code];
    };
    expect(await code({ doc_type: "driving_licence", number: "x" })).toEqual([
      422,
      "partners.doc_type_invalid",
    ]);
    expect(
      await code({ doc_type: "id_proof", id_kind: "aadhaar", number: "123412341234" }),
    ).toEqual([422, "partners.aadhaar_not_accepted"]);
    expect(
      await code({ doc_type: "id_proof", id_kind: "voter_id", number: "1234 1234 1234" }),
    ).toEqual([422, "partners.aadhaar_not_accepted"]);
    const badPan = await send({ application_id: app.id, doc_type: "pan", number: "ABC123" });
    expect(await badPan.json()).toMatchObject({ code: "partners.number_invalid", field: "number" });
    expect(
      await code({ doc_type: "fssai", number: "11521999000123", valid_until: "2020-01-01" }),
    ).toEqual([422, "partners.document_expired"]);
    expect(await code({ doc_type: "kitchen_photos", file_keys: [file] })).toEqual([
      422,
      "partners.file_key_invalid",
    ]);
    expect(
      await code({ doc_type: "pan", number: "ABCDE1234F", file_keys: ["media/x.jpg"] }),
    ).toEqual([422, "partners.file_key_invalid"]);
    const pan = await send({
      application_id: app.id,
      doc_type: "pan",
      number: "abcde 1234f",
      file_keys: [file],
    });
    expect(pan.status).toBe(201);
    expect(await pan.json()).toMatchObject({
      doc_type: "pan",
      number_masked: "XXXXXX234F",
      file_count: 1,
      check: { status: "passed", code: "format_ok" },
      review: { status: "pending", reason: null },
    });
    expect(JSON.stringify(localStorage)).not.toContain("ABCDE1234F");
    const bank = await send({
      application_id: app.id,
      doc_type: "bank",
      number: "123456780000",
      ifsc: "SBIN0001234",
      account_holder: "Priya",
    });
    expect(await bank.json()).toMatchObject({
      number_masked: "XXXXXXXX0000",
      details: { ifsc: "SBIN0001234" },
      check: { status: "failed", code: "penny_drop_failed" },
    });
    const noHolder = await send({
      application_id: app.id,
      doc_type: "bank",
      number: "123456789",
      ifsc: "SBIN0001234",
    });
    expect((await noHolder.json()).code).toBe("request.invalid");
    const fssai = await send({
      application_id: app.id,
      doc_type: "fssai",
      number: "11521999000123",
      valid_until: "2099-01-01",
    });
    expect(await fssai.json()).toMatchObject({ fssai_reg_no: "11521999000123" });
    const detail = await (await call("GET", `/v1/partner-applications/${app.id}`)).json();
    expect(
      detail.documents.map((/** @type {{ doc_type: string }} */ d) => d.doc_type).sort(),
    ).toEqual(["bank", "fssai", "pan"]);
    expect(JSON.stringify(detail)).not.toContain("number_hash");
    expect(
      detail.requirements.documents.find(
        (/** @type {{ doc_type: string }} */ d) => d.doc_type === "pan",
      ),
    ).toMatchObject({
      status: "submitted",
    });
    expect((await call("POST", "/v1/kyc-documents", { application_id: app.id }, {})).status).toBe(
      400,
    );
    expect((await send({ application_id: "nope", doc_type: "pan" })).status).toBe(404);
  });

  it("goes from documents to the hub's review, then training, to verified", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    await signIn();
    const app = await apply("captain");
    const file = await upload();
    await send({
      application_id: app.id,
      doc_type: "id_proof",
      id_kind: "voter_id",
      number: "XYZ1234567",
      file_keys: [file],
    });
    await send({ application_id: app.id, doc_type: "pan", number: "ABCDE1234F" });
    expect((await (await call("GET", "/v1/partner-applications/me")).json()).status).toBe(
      "applied",
    );
    await send({
      application_id: app.id,
      doc_type: "bank",
      number: "123456789012",
      ifsc: "SBIN0001234",
      account_holder: "Priya",
    });
    expect(await (await call("GET", "/v1/partner-applications/me")).json()).toMatchObject({
      status: "kyc_pending",
      next_step: "review",
    });
    // Training runs alongside the review.
    const trainings = (await (await call("GET", "/v1/trainings")).json()).items;
    expect(trainings.map((/** @type {{ code: string }} */ m) => m.code)).toEqual(REQUIRED.captain);
    const answersFor = (/** @type {string} */ code, right = true) =>
      (MODULES.find((m) => m.code === code)?.questions ?? []).map(([, options, answer], i) => ({
        question_id: `q${i + 1}`,
        option: right ? answer : (answer + 1) % options.length,
      }));
    for (const code of REQUIRED.captain) {
      const res = await call(
        "POST",
        `/v1/trainings/${code}/complete`,
        { version: 1, answers: answersFor(code) },
        key(),
      );
      expect(await res.json()).toMatchObject({ passed: true, status: "passed", score: 1 });
    }
    // Both modules passed, but the hub has not approved yet.
    expect((await (await call("GET", "/v1/partner-applications/me")).json()).status).toBe(
      "kyc_pending",
    );
    vi.setSystemTime(Date.now() + 20_000);
    const verified = await (await call("GET", "/v1/partner-applications/me")).json();
    expect(verified).toMatchObject({ status: "verified", next_step: null });
    expect(verified.partner_id).toEqual(expect.any(String));
    expect(verified.verified_at).toEqual(expect.any(String));
    const docs = (await (await call("GET", `/v1/partner-applications/${app.id}`)).json()).documents;
    expect(
      docs.every(
        (/** @type {{ review: { status: string } }} */ d) => d.review.status === "accepted",
      ),
    ).toBe(true);
    // Verified partners may apply for another kind.
    expect((await apply("rider")).status).toBe("applied");
  });

  it("sends back a document that failed its check, and takes a replacement", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    await signIn();
    const app = await apply("packer");
    await send({
      application_id: app.id,
      doc_type: "id_proof",
      id_kind: "passport",
      number: "A1234567",
    });
    const bad = {
      application_id: app.id,
      doc_type: "bank",
      number: "123456780000",
      ifsc: "SBIN0001234",
      account_holder: "Priya",
    };
    await send(bad);
    vi.setSystemTime(Date.now() + 20_000);
    const back = await (await call("GET", "/v1/partner-applications/me")).json();
    expect(back).toMatchObject({ status: "kyc_pending", next_step: "documents" });
    expect(back.requirements.documents[1]).toMatchObject({
      doc_type: "bank",
      status: "rejected",
      reason: expect.any(String),
    });
    await send({ ...bad, number: "123456789012" });
    expect(await (await call("GET", "/v1/partner-applications/me")).json()).toMatchObject({
      next_step: "review",
    });
    vi.setSystemTime(Date.now() + 20_000);
    expect((await (await call("GET", "/v1/partner-applications/me")).json()).status).toBe(
      "training",
    );
  });
});

describe("training (M8 §3.2)", () => {
  const module = (/** @type {string} */ code) =>
    /** @type {NonNullable<typeof MODULES[number]>} */ (MODULES.find((m) => m.code === code));
  const answers = (/** @type {string} */ code, right = true) =>
    module(code).questions.map(([, options, answer], i) => ({
      question_id: `q${i + 1}`,
      option: right ? answer : (answer + 1) % options.length,
    }));

  it("lists nothing for someone with no application, and never sends the right answers", async () => {
    await signIn();
    expect(await (await call("GET", "/v1/trainings")).json()).toEqual({ items: [] });
    await apply("rider");
    const { items } = await (await call("GET", "/v1/trainings")).json();
    expect(items.map((/** @type {{ code: string }} */ m) => m.code)).toEqual([
      "app_basics",
      "rider_safety",
    ]);
    expect(items[1]).toMatchObject({
      version: 1,
      pass_score: 0.8,
      status: "todo",
      best_score: null,
      passed_at: null,
      video_url: null,
      for_kinds: ["rider"],
    });
    expect(items[1].questions).toHaveLength(5);
    expect(Object.keys(items[1].questions[0]).sort()).toEqual(["id", "options", "text"]);
    expect(typeof items[1].questions[0].options[0]).toBe("string");
    expect(JSON.stringify(items)).not.toContain('"answer"');
    const hi = await (
      await call("GET", "/v1/trainings", undefined, { "accept-language": "hi-IN" })
    ).json();
    expect(hi.items[1].title).toBe("सुरक्षित ड्राइविंग और क्रेट");
    // Marathi has no texts yet and falls back to English.
    const mr = await (
      await call("GET", "/v1/trainings", undefined, { "accept-language": "mr-IN" })
    ).json();
    expect(mr.items[1].title).toBe("Safe driving and crates");
  });

  it("scores on the server at 80%, keeps a pass, and allows five tries a day", async () => {
    await signIn();
    await apply("rider");
    const complete = (
      /** @type {object} */ body,
      /** @type {Record<string, string>} */ headers = key(),
    ) => call("POST", "/v1/trainings/rider_safety/complete", body, headers);
    expect((await complete({ version: 1, answers: answers("rider_safety") }, {})).status).toBe(400);
    expect((await complete({ version: 2, answers: answers("rider_safety") })).status).toBe(409);
    const stale = await (await complete({ version: 2, answers: [] })).json();
    expect(stale.code).toBe("partners.training_version_stale");
    expect(
      (await (await complete({ version: 1, answers: answers("rider_safety").slice(1) })).json())
        .code,
    ).toBe("partners.answers_invalid");
    expect(
      (
        await (
          await complete({
            version: 1,
            answers: [...answers("rider_safety").slice(1), { question_id: "q1", option: 9 }],
          })
        ).json()
      ).code,
    ).toBe("partners.answers_invalid");
    expect(
      (await call("POST", "/v1/trainings/hygiene/complete", { version: 1, answers: [] }, key()))
        .status,
    ).toBe(404);
    // 4 of 5 is exactly 80%: passed.
    const four = answers("rider_safety").map((a, i) =>
      i === 0 ? { ...a, option: (a.option + 1) % 3 } : a,
    );
    const k = key();
    const pass = await complete({ version: 1, answers: four }, k);
    expect(await pass.json()).toMatchObject({
      code: "rider_safety",
      score: 0.8,
      correct: 4,
      total: 5,
      passed: true,
      status: "passed",
      application: { status: "applied", next_step: "documents" },
    });
    expect((await (await complete({ version: 1, answers: four }, k)).json()).correct).toBe(4);
    // A later bad try never undoes a pass or the best score, and tries are counted.
    for (let n = 0; n < 4; n++) {
      const bad = await (
        await complete({ version: 1, answers: answers("rider_safety", false) })
      ).json();
      expect(bad).toMatchObject({ passed: false, correct: 0, status: "passed" });
    }
    const sixth = await complete({ version: 1, answers: answers("rider_safety") });
    expect(sixth.status).toBe(429);
    expect((await sixth.json()).code).toBe("partners.training_attempts_exceeded");
    const mine = (await (await call("GET", "/v1/trainings")).json()).items.find(
      (/** @type {{ code: string }} */ m) => m.code === "rider_safety",
    );
    expect(mine).toMatchObject({
      status: "passed",
      best_score: 0.8,
      passed_at: expect.any(String),
    });
  });

  it("serves the backend seed's kitchen with every required module passed", async () => {
    await signIn("+919800001005");
    const { items } = await (await call("GET", "/v1/trainings")).json();
    expect(items.map((/** @type {{ code: string }} */ m) => m.code)).toEqual(REQUIRED.kitchen);
    expect(items.every((/** @type {{ status: string }} */ m) => m.status === "passed")).toBe(true);
    expect(items[1]).toMatchObject({ code: "hygiene", best_score: 0.8 });
  });
});

describe("Know your cook card (M8 §3.5)", () => {
  it("shows the kitchen's checks and no phone, address or KYC detail", async () => {
    const res = await call("GET", "/v1/partners/kit-lata/card");
    expect(res.status).toBe(200);
    const card = await res.json();
    expect(Object.keys(card).sort()).toEqual(
      "area display_name fssai_reg_no fssai_valid_until hygiene partner_id photo_url portions_served rating since skills".split(
        " ",
      ),
    );
    expect(card).toMatchObject({
      since: "2024-03",
      hygiene: { grade: "A" },
      rating: { count: 23 },
      photo_url: null,
    });
    expect((await call("GET", "/v1/partners/nope/card")).status).toBe(404);
  });
});

describe("earnings (M8 §3.6)", () => {
  const istToday = () =>
    new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
  const weeksAgo = (/** @type {number} */ n) =>
    isoWeek(
      new Date(Date.parse(`${istToday()}T00:00:00Z`) - n * 7 * 86_400_000)
        .toISOString()
        .slice(0, 10),
    );
  const earnings = (/** @type {string} */ query = "") => call("GET", `/v1/earnings${query}`);

  it("works out ISO weeks around the new year", () => {
    expect(isoWeek("2026-01-01")).toBe("2026-W01");
    expect(isoWeek("2026-12-31")).toBe("2026-W53");
    expect(isoWeek("2027-01-03")).toBe("2026-W53");
    expect(isoWeek("2027-01-04")).toBe("2027-W01");
  });

  it("gives a kitchen's last week from the rate card, with the payout that followed", async () => {
    await signIn("+919800001005");
    const week = weeksAgo(1);
    const res = await earnings(`?week=${week}`);
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("no-store");
    const e = await res.json();
    expect(e).toMatchObject({
      week,
      total_paise: 204_000,
      by_kind: [{ kind: "kitchen", basis: "tiffin_portion", units: 34, amount_paise: 204_000 }],
      payouts: [
        {
          kind: "kitchen",
          amount_paise: 204_000,
          status: "processed",
          hold_reason: null,
          provider: "simulated",
        },
      ],
    });
    expect(e.days).toHaveLength(7);
    expect(
      e.days.reduce(
        (/** @type {number} */ n, /** @type {{ amount_paise: number }} */ d) => n + d.amount_paise,
        0,
      ),
    ).toBe(204_000);
    expect(e.entries[0].at > e.entries[1].at).toBe(true);
    expect(e.entries[0]).toMatchObject({
      kind: "kitchen",
      basis: "tiffin_portion",
      ref: { type: "order" },
    });
    expect(e.days[0].date).toBe(e.from);
    expect(e.days[6].date).toBe(e.to);
  });

  it("defaults to this week, which is not paid out yet, and pays riders, captains and packers by their basis", async () => {
    await signIn("+919800001006");
    const now = await (await earnings(`?week=${weeksAgo(0)}`)).json();
    expect(now.payouts).toEqual([]);
    const last = await (await earnings(`?week=${weeksAgo(1)}`)).json();
    expect(last).toMatchObject({
      total_paise: 90 * 800,
      by_kind: [{ kind: "rider", basis: "dispatched_order", units: 90 }],
    });
    await signIn("+919800001004");
    expect((await (await earnings(`?week=${weeksAgo(1)}`)).json()).by_kind[0]).toMatchObject({
      basis: "bag_delivered",
      amount_paise: 90 * 600,
    });
    await signIn("+919800001007");
    expect((await (await earnings(`?week=${weeksAgo(1)}`)).json()).by_kind[0]).toMatchObject({
      basis: "packed_order",
      units: 173,
    });
  });

  it("refuses a malformed or future week, and anyone who is not a partner", async () => {
    await signIn("+919800001005");
    for (const bad of ["2026-42", "nope", "2999-W01"]) {
      const res = await earnings(`?week=${bad}`);
      expect(res.status).toBe(422);
      expect((await res.json()).code).toBe("partners.week_invalid");
    }
    await signIn();
    const res = await earnings(`?week=${weeksAgo(1)}`);
    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("auth.forbidden");
  });
});
