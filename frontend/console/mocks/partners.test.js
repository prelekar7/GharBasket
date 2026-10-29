// The partner mocks answer with the real M8 shapes and rules (docs/modules/M8-partners.md §3.1, §3.3, §3.4).
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { PARTNER_ID } from "./partner-ids.js";
import { partnersHandlers } from "./partners.js";

/**
 * @param {string} method @param {string} path
 * @param {{ body?: unknown, role?: string, key?: string }} [options]
 */
async function call(method, path, { body, role = "hub_manager", key } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (body !== undefined) headers["content-type"] = "application/json";
  if (key) headers["idempotency-key"] = key;
  const response = await getResponse(
    partnersHandlers,
    new Request(`http://mock.gharbasket.local${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  assert.ok(response, `no handler for ${method} ${path}`);
  return { status: response.status, body: /** @type {any} */ (await response.json()) };
}

const HUB = `/v1/hubs/${KHARGHAR_HUB_ID}`;

test("the review queue lists kyc_pending applications, oldest first, with the review fields", async () => {
  const { status, body } = await call("GET", `${HUB}/partner-applications`);
  assert.equal(status, 200);
  assert.ok(/** @type {any[]} */ (body.items).length >= 2);
  assert.ok(/** @type {any[]} */ (body.items).every((a) => a.status === "kyc_pending"));
  const times = /** @type {any[]} */ (body.items).map((a) => a.submitted_at);
  assert.deepEqual(times, [...times].sort());
  const [first] = body.items;
  assert.ok(
    first.applicant.name &&
      /** @type {any[]} */ (first.documents).length &&
      first.requirements.documents.length,
  );
  assert.ok(
    /** @type {any[]} */ (first.documents).every((d) => !("number" in d) && !("number_enc" in d)),
  );
  const missing = await call("GET", `${HUB}/partner-applications?status=applied`);
  assert.equal(/** @type {any[]} */ (missing.body.items).length, 1);
  assert.equal(missing.body.items[0].next_step, "documents");
});

test("a decision needs an Idempotency-Key, a reason to reject, and a hygiene grade to approve a kitchen", async () => {
  const { body } = await call("GET", `${HUB}/partner-applications`);
  const kitchen = /** @type {any[]} */ (body.items).find((a) => a.kind === "kitchen");
  const url = `/v1/partner-applications/${kitchen.id}/decision`;
  assert.equal((await call("POST", url, { body: { outcome: "approve" } })).status, 400);
  const noReason = await call("POST", url, { body: { outcome: "reject" }, key: "d1" });
  assert.equal(noReason.status, 422);
  assert.equal(noReason.body.code, "partners.reason_required");
  const noGrade = await call("POST", url, { body: { outcome: "approve" }, key: "d2" });
  assert.equal(noGrade.body.code, "partners.reason_required");
  const bad = await call("POST", url, { body: { outcome: "maybe" }, key: "d3" });
  assert.equal(bad.body.code, "request.invalid");
  assert.deepEqual(bad.body.errors[0].loc, ["body", "outcome"]);
});

test("approving with training done verifies the person, and a decided application leaves the queue", async () => {
  const { body } = await call("GET", `${HUB}/partner-applications?kind=kitchen`);
  const kitchen = body.items[0];
  const url = `/v1/partner-applications/${kitchen.id}/decision`;
  const done = await call("POST", url, {
    body: { outcome: "approve", hygiene_grade: "A" },
    key: "ok1",
  });
  assert.equal(done.status, 200);
  assert.equal(done.body.status, "verified");
  assert.ok(done.body.partner_id && done.body.verified_at);
  assert.ok(
    /** @type {any[]} */ (done.body.documents).every((d) => d.review.status === "accepted"),
  );
  assert.deepEqual(
    (await call("POST", url, { body: { outcome: "approve", hygiene_grade: "A" }, key: "ok1" }))
      .body,
    done.body,
  );
  const again = await call("POST", url, {
    body: { outcome: "approve", hygiene_grade: "A" },
    key: "ok2",
  });
  assert.equal(again.body.code, "partners.not_reviewable");
  const partners = await call("GET", `${HUB}/partners?kind=kitchen`);
  assert.ok(/** @type {any[]} */ (partners.body.items).some((p) => p.id === done.body.partner_id));
  assert.equal((await call("GET", `/v1/partners/${done.body.partner_id}/card`)).status, 200);
});

test("an application whose documents are not all in cannot be decided", async () => {
  const { body } = await call("GET", `${HUB}/partner-applications?status=applied`);
  const res = await call("POST", `/v1/partner-applications/${body.items[0].id}/decision`, {
    body: { outcome: "reject", reason: "no" },
    key: "ap1",
  });
  assert.equal(res.body.code, "partners.not_reviewable");
});

test("suspend needs a reason code and a note, reinstate undoes it, and each is refused twice", async () => {
  const suspend = `/v1/partners/${PARTNER_ID.kavita}/suspend`;
  const empty = await call("POST", suspend, {
    body: { reason_code: "other", note: " " },
    key: "s0",
  });
  assert.equal(empty.status, 422);
  assert.deepEqual(empty.body.errors[0].loc, ["body", "note"]);
  const ok = await call("POST", suspend, {
    body: { reason_code: "conduct", note: "Missed two shifts" },
    key: "s1",
  });
  assert.equal(ok.body.status, "suspended");
  assert.equal(ok.body.suspension.reason_code, "conduct");
  assert.equal(
    (await call("POST", suspend, { body: { reason_code: "conduct", note: "again" }, key: "s2" }))
      .body.code,
    "partners.already_suspended",
  );
  const back = await call("POST", `/v1/partners/${PARTNER_ID.kavita}/reinstate`, {
    body: {},
    key: "r1",
  });
  assert.equal(back.body.status, "active");
  assert.equal(back.body.suspension, null);
  assert.equal(
    (await call("POST", `/v1/partners/${PARTNER_ID.kavita}/reinstate`, { body: {}, key: "r2" }))
      .body.code,
    "partners.not_suspended",
  );
});

test("the card shows a kitchen's public facts only, and an inspection changes its grade", async () => {
  const card = await call("GET", `/v1/partners/${PARTNER_ID.lata}/card`);
  assert.equal(card.status, 200);
  assert.ok(card.body.fssai_reg_no);
  for (const secret of ["address", "phone_e164", "location", "user_id"])
    assert.ok(!(secret in card.body));
  assert.equal(
    (await call("GET", `/v1/partners/${PARTNER_ID.arjun}/card`)).body.code,
    "partners.partner_not_found",
  );
  const inspection = await call("POST", `/v1/kitchens/${PARTNER_ID.lata}/inspection`, {
    body: { grade: "B", note: null },
    key: "i1",
  });
  assert.equal(inspection.body.hygiene.grade, "B");
  assert.equal((await call("GET", `/v1/partners/${PARTNER_ID.lata}/card`)).body.hygiene.grade, "B");
});

test("a franchise owner may read partners but not decide or suspend", async () => {
  assert.equal((await call("GET", `${HUB}/partners`, { role: "franchise_owner" })).status, 200);
  const refused = await call("POST", `/v1/partners/${PARTNER_ID.lata}/suspend`, {
    body: { reason_code: "other", note: "x" },
    key: "f1",
    role: "franchise_owner",
  });
  assert.equal(refused.status, 403);
});

test("the fairness read is the contract's FairnessOut: the λ in use and the runs, newest first", async () => {
  const { status, body } = await call("GET", `${HUB}/fairness`);
  assert.equal(status, 200);
  assert.deepEqual(Object.keys(body).sort(), ["fallback_active", "hub_id", "lambda_paise", "runs"]);
  assert.equal(body.lambda_paise, 300);
  assert.equal(body.runs.length, 28);
  assert.deepEqual(Object.keys(body.runs[0]).sort(), [
    "at",
    "date",
    "gini",
    "gini_eligible",
    "kitchens_used",
    "km_per_portion",
    "lambda_paise",
    "meal",
    "model",
    "portions",
    "round",
    "run_id",
    "source",
    "status",
    "unassigned",
  ]);
  const times = /** @type {any[]} */ (body.runs).map((r) => r.at);
  assert.deepEqual(times, [...times].sort().reverse());
  assert.equal(body.fallback_active, body.runs[0].model === "greedy-m8");
  assert.ok(body.runs.some((/** @type {any} */ r) => r.gini === null && r.lambda_paise === 300));
  assert.ok(body.runs.some((/** @type {any} */ r) => r.lambda_paise === null));
  assert.equal((await call("GET", `${HUB}/fairness?runs=3`)).body.runs.length, 3);
});

test("the fairness read refuses other hubs, other roles and a bad runs value", async () => {
  const other = "/v1/hubs/0199a000-0000-7000-8000-00000000a002/fairness";
  assert.equal((await call("GET", other)).body.code, "auth.forbidden");
  assert.equal((await call("GET", `${HUB}/fairness`, { role: "ngo_coordinator" })).status, 403);
  assert.equal((await call("GET", `${HUB}/fairness?runs=0`)).body.code, "request.invalid");
  assert.equal((await call("GET", `${HUB}/fairness?runs=101`)).status, 422);
});
