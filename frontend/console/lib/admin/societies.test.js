import assert from "node:assert/strict";
import { test } from "node:test";
import { changesOf, createBody, patchBody, statusChoices } from "./societies.js";

test("a create body never carries status, slug or boundary, and leaves out what is not set", () => {
  const body = createBody({
    name: "Palm Court",
    area: "Kamothe",
    hub_id: undefined,
    unlock_threshold: undefined,
    flats: 120,
    towers: [{ label: "A wing" }],
    status: "live",
    slug: "x",
    boundary: { type: "Polygon", coordinates: [] },
  });
  assert.deepEqual(body, {
    name: "Palm Court",
    area: "Kamothe",
    flats: 120,
    towers: [{ label: "A wing" }],
  });
});

test("a patch body never carries name, area, hub_id, flats or status prospect; boundary null is kept", () => {
  const body = patchBody({
    name: "X",
    area: "Y",
    hub_id: "h",
    flats: 5,
    status: "prospect",
    boundary: null,
    unlock_threshold: 25,
  });
  assert.deepEqual(body, { boundary: null, unlock_threshold: 25 });
  assert.deepEqual(patchBody({ status: "live" }), { status: "live" });
});

test("a prospect can be moved on but never chosen again", () => {
  assert.deepEqual(statusChoices("prospect"), ["prospect", "unlocking", "paused", "live"]);
  assert.deepEqual(statusChoices("live"), ["unlocking", "paused", "live"]);
});

const society = {
  status: "unlocking",
  unlock_threshold: 25,
  towers: [
    { id: "a", label: "A wing" },
    { id: "b", label: "B wing" },
  ],
};

test("an edit sends only what changed, so an untouched threshold keeps its source", () => {
  assert.deepEqual(changesOf(society, { ...society }), {});
  assert.deepEqual(changesOf(society, { ...society, unlock_threshold: 30 }), {
    unlock_threshold: 30,
  });
  assert.deepEqual(changesOf(society, { ...society, status: "paused" }), { status: "paused" });
  // a prospect left as a prospect is not a change
  assert.deepEqual(
    changesOf({ ...society, status: "prospect" }, { ...society, status: "prospect" }),
    {},
  );
});

test("towers are sent whole when any changed: existing ids kept, new ones without an id", () => {
  const edited = [{ id: "a", label: "Tower 1" }, society.towers[1], { label: "C wing" }];
  assert.deepEqual(changesOf(society, { ...society, towers: edited }), {
    towers: [{ id: "a", label: "Tower 1" }, { id: "b", label: "B wing" }, { label: "C wing" }],
  });
  assert.deepEqual(changesOf(society, { ...society, towers: [] }), { towers: [] });
});
