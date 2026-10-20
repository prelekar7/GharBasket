import assert from "node:assert/strict";
import { test } from "node:test";
import { canAccess, itemFor, navFor, paletteEntries } from "./nav.js";
import { parseSession, ROLES, STAFF_ROLES, staffRoleOf } from "./session.js";

/** @param {string} role */
const grant = (role) => ({ role, scope_type: null, scope_id: null, status: "verified" });

test("the catalog editor is a staff role, lands on the catalog and is not a mock sign-in", () => {
  assert.ok(STAFF_ROLES.includes("catalog_editor"));
  assert.equal(parseSession("catalog_editor"), "catalog_editor");
  assert.equal(ROLES.catalog_editor.home, "/catalog");
  assert.equal(ROLES.catalog_editor.phone, "9800001013");
  assert.equal(ROLES.catalog_editor.realOnly, true);
  assert.equal(staffRoleOf([grant("customer"), grant("catalog_editor")]), "catalog_editor");
  // Best role first: an admin who is also an editor works as admin.
  assert.equal(staffRoleOf([grant("catalog_editor"), grant("admin")]), "admin");
});

test("the catalog editor sees only the catalog and the prices", () => {
  const groups = navFor("catalog_editor");
  assert.deepEqual(
    groups.map((g) => g.id),
    ["catalog"],
  );
  assert.deepEqual(
    groups[0].items.map((i) => i.href),
    ["/catalog", "/prices"],
  );
  for (const path of ["/catalog", "/catalog/okra", "/prices"])
    assert.ok(canAccess("catalog_editor", /** @type {any} */ (itemFor(path))), path);
  for (const path of ["/prices/publish", "/users", "/hubs", "/today", "/pnl", "/handoffs"])
    assert.ok(!canAccess("catalog_editor", /** @type {any} */ (itemFor(path))), path);
  assert.deepEqual(
    paletteEntries("catalog_editor").actions,
    [],
    "no quick action (publish a price list, give a role, ...) is open to her",
  );
});

test("the admin and the hub manager keep what they saw before the catalog got its own section", () => {
  const admin = navFor("admin");
  assert.equal(admin[0].id, "catalog");
  assert.ok(admin.some((g) => g.id === "admin"));
  const hub = navFor("hub_manager").flatMap((g) => g.items.map((i) => i.href));
  assert.ok(hub.includes("/prices"));
  assert.ok(!hub.includes("/catalog"));
  assert.ok(canAccess("hub_manager", /** @type {any} */ (itemFor("/prices/publish"))));
});
