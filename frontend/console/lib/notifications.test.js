import assert from "node:assert/strict";
import { test } from "node:test";
import {
  entriesOf,
  kindName,
  missingLocales,
  templateIn,
  templateProblems,
} from "./notifications.js";

const row = (/** @type {Record<string, unknown>} */ o) => ({
  id: "x",
  kind: "orders.locked",
  event_type: "orders.locked",
  channel: "app",
  locale: "en-IN",
  version: 2,
  title: "T",
  body: "B",
  wa_template_name: null,
  active: true,
  created_by: null,
  created_at: "2026-10-08T12:00:00Z",
  ...o,
});
const kinds = [
  {
    kind: "orders.locked",
    event_type: "orders.locked",
    category: "money",
    placeholders: ["total", "day"],
  },
  {
    kind: "orders.delivered",
    event_type: "orders.delivered",
    category: "delivery",
    placeholders: [],
  },
];

test("kinds read as names", () => {
  assert.equal(kindName("orders.at_society"), "At society");
  assert.equal(kindName("orders.issue_resolved.credited"), "Issue resolved (credited)");
});

test("one entry per kind and channel; a kind with no copy gets an empty app entry", () => {
  const entries = entriesOf({
    kinds,
    items: [
      row({ locale: "en-IN", title: "Box confirmed" }),
      row({ locale: "mr-IN", title: "मराठी" }),
      row({ channel: "whatsapp", wa_template_name: "box_confirmed", title: "", body: "wa" }),
    ],
  });
  assert.deepEqual(
    entries.map((e) => e.id),
    ["orders.locked/app", "orders.locked/whatsapp", "orders.delivered/app"],
  );
  assert.equal(entries[0].version, 2);
  assert.equal(entries[0].texts["en-IN"].title, "Box confirmed");
  assert.equal(entries[0].texts["hi-IN"].body, "");
  assert.deepEqual(entries[0].variables, ["total", "day"]);
  assert.equal(entries[1].wa_template_name, "box_confirmed");
  assert.equal(entries[2].version, 0);
});

test("the PUT body has all three locales; WhatsApp has no titles, app has no template name", () => {
  const texts = {
    "en-IN": { title: "a", body: "b" },
    "hi-IN": { title: "c", body: "d" },
    "mr-IN": { title: "e", body: "f" },
  };
  const app = templateIn({ kind: "orders.locked", channel: "app" }, { wa: "ignored", texts });
  assert.equal(app.wa_template_name, null);
  assert.deepEqual(
    app.texts.map((t) => t.locale),
    ["en-IN", "hi-IN", "mr-IN"],
  );
  const wa = templateIn(
    { kind: "orders.locked", channel: "whatsapp" },
    { wa: " box_confirmed ", texts },
  );
  assert.equal(wa.wa_template_name, "box_confirmed");
  assert.deepEqual(
    wa.texts.map((t) => t.title),
    ["", "", ""],
  );
  assert.deepEqual(missingLocales("app", { ...texts, "mr-IN": { title: "", body: "f" } }), [
    "mr-IN",
  ]);
  assert.deepEqual(missingLocales("whatsapp", { ...texts, "mr-IN": { title: "", body: "f" } }), []);
});

test("template_invalid errors find their locale from the texts index", () => {
  assert.deepEqual(
    templateProblems([
      {
        loc: ["body", "texts", 1, "body"],
        message: "{x} is not a placeholder",
        type: "template_invalid",
      },
      { loc: ["body", "wa_template_name"], message: "name needed", type: "template_invalid" },
    ]),
    [
      { locale: "hi-IN", message: "{x} is not a placeholder" },
      { locale: null, message: "name needed" },
    ],
  );
  assert.deepEqual(templateProblems(undefined), []);
});
