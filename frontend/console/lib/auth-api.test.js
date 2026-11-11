import assert from "node:assert/strict";
import { test } from "node:test";
import { cookieValue, exchangeBody, parseSetCookie, signInRefusal } from "./auth-api.js";
import { staffRoleOf } from "./session.js";

// The lines the dev API (8010) sends on POST /v1/auth/exchange.
const ACCESS = "__Host-gb_at=aaa.bbb.ccc; HttpOnly; Max-Age=900; Path=/; SameSite=lax; Secure";
const REFRESH = "gb_rt=ZXzb-Mr_Jk1; HttpOnly; Max-Age=2591999; Path=/v1/auth; SameSite=lax; Secure";

test("Set-Cookie lines keep their name, value and attributes", () => {
  assert.deepEqual(parseSetCookie(ACCESS), {
    name: "__Host-gb_at",
    value: "aaa.bbb.ccc",
    options: { httpOnly: true, maxAge: 900, path: "/", sameSite: "lax", secure: true },
  });
  assert.equal(parseSetCookie(REFRESH).options.path, "/v1/auth");
  assert.equal(cookieValue([ACCESS, REFRESH], "gb_rt"), "ZXzb-Mr_Jk1");
  assert.equal(cookieValue([ACCESS], "gb_rt"), null);
});

test("the staff role is the preferred one if held, else the best, and only verified grants count", () => {
  const grant = (/** @type {string} */ role, status = "verified") => ({
    role,
    scope_type: null,
    scope_id: null,
    status,
  });
  const both = [grant("customer"), grant("franchise_owner"), grant("hub_manager")];
  assert.equal(staffRoleOf(both), "hub_manager");
  assert.equal(staffRoleOf(both, "franchise_owner"), "franchise_owner");
  assert.equal(staffRoleOf(both, "admin"), "hub_manager");
  assert.equal(staffRoleOf([grant("customer"), grant("rider")]), null);
  assert.equal(staffRoleOf([grant("admin", "suspended")]), null);
});

test("the console's exchange always asks for staff only (M1 §3.1), whatever proves the phone", () => {
  assert.deepEqual(exchangeBody({ dev_phone: "+919800001008" }), {
    dev_phone: "+919800001008",
    staff_only: true,
    device: { platform: "web" },
  });
  assert.equal(exchangeBody({ firebase_id_token: "eyJ" }).staff_only, true);
});

test("a number that is not staff goes to /not-staff; other refusals are a sentence", () => {
  assert.equal(signInRefusal("auth.not_staff"), "not_staff");
  assert.equal(signInRefusal("auth.rate_limited"), "Too many attempts. Wait a few minutes.");
  assert.match(signInRefusal("auth.provider_unavailable"), /Try again in a minute/);
  assert.match(signInRefusal(undefined), /could not sign you in/);
  assert.match(signInRefusal("identity.deletion_pending"), /could not sign you in/);
});
