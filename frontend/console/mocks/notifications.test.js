// The notification mocks answer with the real M11 §3.2 shapes and rules.
import assert from "node:assert/strict";
import { test } from "node:test";
import { getResponse } from "msw";
import { notificationsHandlers } from "./notifications.js";

const TEMPLATES = "/v1/admin/notification-templates";

/**
 * Sends the request to the handlers; undefined when none matches (a route the real API lacks).
 * @param {string} method @param {string} path
 * @param {{ body?: unknown, role?: string }} [options]
 */
async function send(method, path, { body, role = "admin" } = {}) {
  const headers = /** @type {Record<string, string>} */ ({ "x-gb-role": role });
  if (body !== undefined) headers["content-type"] = "application/json";
  return getResponse(
    notificationsHandlers,
    new Request(`http://mock.gharbasket.local${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}

/** @param {Parameters<typeof send>} args */
async function call(...args) {
  const response = await send(...args);
  assert.ok(response, `no handler for ${args[0]} ${args[1]}`);
  return {
    status: response.status,
    body: await response.json(),
    type: response.headers.get("content-type"),
  };
}

/** @param {object} o */
const keys = (o) => Object.keys(o).sort();
const texts = (/** @type {string[][]} */ t) =>
  ["en-IN", "hi-IN", "mr-IN"].map((locale, i) => ({ locale, title: t[i][0], body: t[i][1] }));
const locked = (/** @type {Record<string, unknown>} */ over = {}) => ({
  kind: "orders.locked",
  channel: "app",
  wa_template_name: null,
  texts: texts([
    ["Box for {day} confirmed", "{total} paid."],
    ["{day} का बॉक्स पक्का", "{total} चुकाए।"],
    ["{day} चा बॉक्स निश्चित", "{total} भरले."],
  ]),
  ...over,
});

test("the template list is { kinds, items } with the real Template fields, newest locale rows by kind", async () => {
  const r = await call("GET", TEMPLATES);
  assert.equal(r.status, 200);
  assert.deepEqual(keys(r.body), ["items", "kinds"]);
  assert.deepEqual(keys(r.body.kinds[0]), ["category", "event_type", "kind", "placeholders"]);
  assert.equal(r.body.kinds.length, 15);
  assert.deepEqual(keys(r.body.items[0]), [
    "active",
    "body",
    "channel",
    "created_at",
    "created_by",
    "event_type",
    "id",
    "kind",
    "locale",
    "title",
    "version",
    "wa_template_name",
  ]);
  assert.ok(r.body.items.every((/** @type {{ active: boolean }} */ t) => t.active));
  const one = await call("GET", `${TEMPLATES}?kind=orders.locked&locale=hi-IN`);
  assert.deepEqual(
    one.body.items.map((/** @type {{ locale: string }} */ t) => t.locale),
    ["hi-IN"],
  );
  assert.equal((await call("GET", TEMPLATES, { role: "hub_manager" })).status, 403);
});

test("PUT on the collection adds the next active version in all three locales; history keeps the old one", async () => {
  const saved = await call("PUT", TEMPLATES, { body: locked() });
  assert.equal(saved.status, 200);
  assert.deepEqual(keys(saved.body), ["items"]);
  assert.deepEqual(
    saved.body.items.map(
      (/** @type {{ locale: string, version: number, active: boolean }} */ t) => [
        t.locale,
        t.version,
        t.active,
      ],
    ),
    [
      ["en-IN", 2, true],
      ["hi-IN", 2, true],
      ["mr-IN", 2, true],
    ],
  );
  assert.equal(saved.body.items[0].title, "Box for {day} confirmed");
  const history = await call(
    "GET",
    `${TEMPLATES}?kind=orders.locked&channel=app&locale=en-IN&history=true`,
  );
  assert.deepEqual(
    history.body.items.map((/** @type {{ version: number, active: boolean }} */ t) => [
      t.version,
      t.active,
    ]),
    [
      [2, true],
      [1, false],
    ],
  );
  // The per-id PUT of the old mock is not a route of the real API.
  assert.equal(
    await send("PUT", `${TEMPLATES}/${saved.body.items[0].id}`, { body: { active: false } }),
    undefined,
  );
});

test("an unusable template is 422 notifications.template_invalid with every reason, located in the body", async () => {
  const bad = locked({
    channel: "whatsapp",
    texts: texts([
      ["", "{nope} and {total:>5}"],
      ["", "{total"],
      ["", "ok {total}"],
    ]).slice(0, 3),
  });
  const r = await call("PUT", TEMPLATES, { body: bad });
  assert.equal(r.status, 422);
  assert.equal(r.type, "application/problem+json");
  assert.equal(r.body.code, "notifications.template_invalid");
  assert.deepEqual(
    r.body.errors.map((/** @type {{ loc: (string | number)[] }} */ e) => e.loc.join(".")),
    ["body.wa_template_name", "body.texts.0.body", "body.texts.0.body", "body.texts.1.body"],
  );
  assert.deepEqual(keys(r.body.errors[0]), ["loc", "message", "type"]);
  const app = await call("PUT", TEMPLATES, { body: locked({ texts: locked().texts.slice(0, 2) }) });
  assert.deepEqual(
    app.body.errors.map((/** @type {{ loc: string[] }} */ e) => e.loc),
    [["body", "texts"]],
  );
  const noTitle = locked();
  noTitle.texts[2].title = " ";
  assert.deepEqual(
    (await call("POST", `${TEMPLATES}/preview`, { body: noTitle })).body.errors[0].loc,
    ["body", "texts", 2, "title"],
  );
  assert.equal(
    (await call("PUT", TEMPLATES, { body: { kind: "orders.locked" } })).body.code,
    "request.invalid",
  );
});

test("preview renders each locale with the sample values and saves nothing", async () => {
  const body = locked({
    channel: "whatsapp",
    wa_template_name: "box_confirmed",
    texts: texts([
      ["", "Your box for {day} is confirmed: {total}."],
      ["", "{total}: {day} का बॉक्स पक्का।"],
      ["", "{day} चा बॉक्स निश्चित: {total}."],
    ]),
  });
  const r = await call("POST", `${TEMPLATES}/preview`, { body });
  assert.equal(r.status, 200);
  assert.deepEqual(
    r.body.items.map((/** @type {Record<string, unknown>} */ t) => [t.locale, t.body, t.params]),
    [
      ["en-IN", "Your box for Tuesday is confirmed: ₹690.", ["Tuesday", "₹690"]],
      ["hi-IN", "₹690: मंगलवार का बॉक्स पक्का।", ["₹690", "मंगलवार"]],
      ["mr-IN", "मंगळवार चा बॉक्स निश्चित: ₹690.", ["मंगळवार", "₹690"]],
    ],
  );
  assert.deepEqual(keys(r.body.items[0]), ["body", "locale", "params", "title"]);
  const time = await call("POST", `${TEMPLATES}/preview`, {
    body: {
      ...locked({ kind: "subscriptions.cutoff_reminder" }),
      texts: texts([
        ["a {cutoff_time}", "b"],
        ["c {cutoff_time}", "d"],
        ["e {cutoff_time}", "f"],
      ]),
    },
  });
  assert.deepEqual(
    time.body.items.map((/** @type {{ title: string }} */ t) => t.title),
    ["a 9 PM", "c रात 9 बजे", "e रात्री 9"],
  );
  const wa = await call("GET", `${TEMPLATES}?kind=orders.locked&channel=whatsapp`);
  assert.deepEqual(wa.body.items, []);
});
