import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { datesBetween, dayPath, groupDays, planFestivalSave } from "./festivals.js";

/** @type {import("./festivals.js").Festival} */
const NAVRATRI = {
  id: "navratri:all:2026-10-11",
  code: "navratri",
  name: "Navratri",
  region: "all",
  starts_on: "2026-10-11",
  ends_on: "2026-10-13",
  rules: { fasting: true, no_onion_garlic: true },
  verified: false,
};

/** @param {Partial<import("./festivals.js").FestivalInput>} [over] @returns {import("./festivals.js").FestivalInput} */
const input = (over = {}) => {
  const { id: _id, ...same } = NAVRATRI;
  return { ...same, ...over };
};

const dates = (/** @type {{ body: { date: string } }[]} */ posts) => posts.map((p) => p.body.date);
const days = (/** @type {{ path: string }[]} */ calls) => calls.map((c) => c.path.split("/")[4]);

describe("datesBetween", () => {
  test("both ends are included, and a backwards range is empty", () => {
    assert.deepEqual(datesBetween("2026-10-30", "2026-11-02"), [
      "2026-10-30",
      "2026-10-31",
      "2026-11-01",
      "2026-11-02",
    ]);
    assert.deepEqual(datesBetween("2026-10-02", "2026-10-01"), []);
  });
});

describe("planFestivalSave", () => {
  test("a new run is one POST per date, each with its own key and the name in all three languages", () => {
    const plan = planFestivalSave(null, input({ name: " Navratri " }), "key-1");
    assert.deepEqual(dates(plan.posts), ["2026-10-11", "2026-10-12", "2026-10-13"]);
    assert.deepEqual(plan.patches, []);
    assert.deepEqual(plan.deletes, []);
    assert.deepEqual(
      plan.posts.map((p) => p.idempotencyKey),
      ["key-1:2026-10-11", "key-1:2026-10-12", "key-1:2026-10-13"],
    );
    assert.deepEqual(plan.posts[0].body, {
      code: "navratri",
      date: "2026-10-11",
      region: "all",
      names: { en: "Navratri", hi: "Navratri", mr: "Navratri" },
      rules: { fasting: true, no_onion_garlic: true },
      verified: false,
    });
  });

  test("saving a run with nothing changed makes no calls", () => {
    const plan = planFestivalSave(NAVRATRI, input(), "k");
    assert.deepEqual(plan, { posts: [], patches: [], deletes: [] });
  });

  test("a changed rule, name or flag is a PATCH of that field on every day, never a POST", () => {
    const plan = planFestivalSave(
      NAVRATRI,
      input({
        name: "Navratri 2026",
        verified: true,
        rules: { fasting: true, no_onion_garlic: false },
      }),
      "k",
    );
    assert.deepEqual(plan.posts, []);
    assert.deepEqual(plan.deletes, []);
    assert.deepEqual(days(plan.patches), ["2026-10-11", "2026-10-12", "2026-10-13"]);
    assert.equal(plan.patches[1].path, "/v1/admin/festivals/2026-10-12/navratri/all");
    assert.deepEqual(plan.patches[1].body, {
      names: { en: "Navratri 2026", hi: "Navratri 2026", mr: "Navratri 2026" },
      rules: { fasting: true, no_onion_garlic: false },
      verified: true,
    });
  });

  test("only the field that changed is sent", () => {
    const plan = planFestivalSave(NAVRATRI, input({ verified: true }), "k");
    assert.deepEqual(
      plan.patches.map((p) => p.body),
      [{ verified: true }, { verified: true }, { verified: true }],
    );
  });

  test("a longer run POSTs the new days and PATCHes the old ones", () => {
    const plan = planFestivalSave(NAVRATRI, input({ ends_on: "2026-10-15", verified: true }), "k");
    assert.deepEqual(dates(plan.posts), ["2026-10-14", "2026-10-15"]);
    assert.deepEqual(days(plan.patches), ["2026-10-11", "2026-10-12", "2026-10-13"]);
    assert.deepEqual(plan.deletes, []);
  });

  test("a shorter run DELETEs the days it no longer covers, at either end", () => {
    const plan = planFestivalSave(
      NAVRATRI,
      input({ starts_on: "2026-10-12", ends_on: "2026-10-12" }),
      "k",
    );
    assert.deepEqual(plan.posts, []);
    assert.deepEqual(plan.patches, []);
    assert.deepEqual(plan.deletes, [
      { path: "/v1/admin/festivals/2026-10-11/navratri/all" },
      { path: "/v1/admin/festivals/2026-10-13/navratri/all" },
    ]);
  });

  test("a moved run POSTs, PATCHes and DELETEs at once", () => {
    const plan = planFestivalSave(
      NAVRATRI,
      input({ starts_on: "2026-10-12", ends_on: "2026-10-14", name: "Navratri 2026" }),
      "k",
    );
    assert.deepEqual(dates(plan.posts), ["2026-10-14"]);
    assert.deepEqual(days(plan.patches), ["2026-10-12", "2026-10-13"]);
    assert.deepEqual(days(plan.deletes), ["2026-10-11"]);
  });

  test("a new range apart from the old one replaces every day", () => {
    const plan = planFestivalSave(
      NAVRATRI,
      input({ starts_on: "2026-11-01", ends_on: "2026-11-02" }),
      "k",
    );
    assert.deepEqual(dates(plan.posts), ["2026-11-01", "2026-11-02"]);
    assert.deepEqual(days(plan.deletes), ["2026-10-11", "2026-10-12", "2026-10-13"]);
  });

  test("a changed code or region is another row: all old days go, all new days are created", () => {
    for (const change of [{ code: "navaratri" }, { region: "maharashtra" }]) {
      const plan = planFestivalSave(NAVRATRI, input({ ...change, ends_on: "2026-10-14" }), "k");
      assert.equal(plan.posts.length, 4);
      assert.deepEqual(plan.patches, []);
      assert.deepEqual(plan.deletes, [
        { path: "/v1/admin/festivals/2026-10-11/navratri/all" },
        { path: "/v1/admin/festivals/2026-10-12/navratri/all" },
        { path: "/v1/admin/festivals/2026-10-13/navratri/all" },
      ]);
    }
  });

  test("code and region are escaped in the path", () => {
    assert.equal(dayPath("2026-10-11", "a/b", "x y"), "/v1/admin/festivals/2026-10-11/a%2Fb/x%20y");
  });

  test("undo is the same plan the other way round", () => {
    const edited = input({ ends_on: "2026-10-15", name: "Navratri 2026" });
    const saved = { ...NAVRATRI, ...edited, id: NAVRATRI.id };
    const { id: _id, ...before } = NAVRATRI;
    const undo = planFestivalSave(saved, before, "k2");
    assert.deepEqual(days(undo.deletes), ["2026-10-14", "2026-10-15"]);
    assert.deepEqual(days(undo.patches), ["2026-10-11", "2026-10-12", "2026-10-13"]);
    assert.deepEqual(undo.patches[0].body, {
      names: { en: "Navratri", hi: "Navratri", mr: "Navratri" },
    });
    assert.deepEqual(undo.posts, []);
  });

  test("the plan covers what groupDays shows: a run of rows saved again unchanged is a no-op", () => {
    const rows = ["2026-10-11", "2026-10-12"].map((date) => ({
      date,
      code: "navratri",
      name: "Navratri",
      region: "all",
      rules: { fasting: true, no_onion_garlic: true },
      verified: false,
    }));
    const [run] = groupDays(rows);
    const { id: _id, ...same } = run;
    assert.deepEqual(planFestivalSave(run, same, "k"), { posts: [], patches: [], deletes: [] });
  });
});
