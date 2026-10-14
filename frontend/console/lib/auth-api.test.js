import assert from "node:assert/strict";
import { test } from "node:test";
import { cookieValue, parseSetCookie } from "./auth-api.js";
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
