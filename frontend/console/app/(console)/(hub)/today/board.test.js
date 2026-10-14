import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { getResponse } from "msw";
import { KHARGHAR_HUB_ID, TALOJA_HUB_ID } from "../../../../mocks/hubs.js";
import { opsBoardHandlers } from "../../../../mocks/opsboard.js";
import {
  attentionRows,
  clock,
  consoleHref,
  statusLine,
  timelineSteps,
  wasteChange,
} from "./board.js";

/** @param {string} hub @param {string | null} role */
const today = async (hub, role) =>
  /** @type {Response} */ (
    await getResponse(
      opsBoardHandlers,
      new Request(`http://mock.gharbasket.local/v1/analytics/hubs/${hub}/today`, {
        headers: role ? { "x-gb-role": role } : {},
      }),
    )
  );

describe("the today mock follows the contract", () => {
  test("the board has the spec's sections and ISO timestamps", async () => {
    const res = await today(KHARGHAR_HUB_ID, "hub_manager");
    assert.equal(res.headers.get("cache-control"), "no-store");
    const b = await res.json();
    assert.deepEqual(Object.keys(b).sort(), [
      "as_of",
      "attention",
      "boxes",
      "date",
      "deliveries",
      "hub_id",
      "packing",
      "routes",
      "shortages",
      "steps",
      "waste",
    ]);
    assert.match(b.as_of, /\+05:30$/);
    assert.equal(
      b.boxes.by_society.reduce(
        (/** @type {number} */ n, /** @type {{ boxes: number }} */ s) => n + s.boxes,
        0,
      ),
      b.boxes.total,
    );
    assert.ok(b.attention.every((/** @type {{ link: string }} */ a) => a.link.startsWith("/hub/")));
  });

  test("a hub outside the caller's grants is 403 auth.forbidden, signed out is 401", async () => {
    const res = await today(TALOJA_HUB_ID, "hub_manager");
    assert.equal(res.status, 403);
    assert.equal((await res.json()).code, "auth.forbidden");
    assert.equal((await today(KHARGHAR_HUB_ID, null)).status, 401);
    assert.equal((await today(KHARGHAR_HUB_ID, "admin")).status, 200);
  });
});

describe("the board's words", () => {
  test("clock", () => {
    assert.equal(clock("21:00"), "9:00 PM");
    assert.equal(clock("06:30"), "6:30 AM");
    assert.equal(clock("12:30"), "12:30 PM");
  });

  test("the status line comes from the steps", async () => {
    const b = await (await today(KHARGHAR_HUB_ID, "admin")).json();
    assert.equal(
      statusLine(b.steps),
      "Now: handovers through captains. Look at: goods received and graded.",
    );
    assert.equal(timelineSteps(b.steps)[0].time, "9:00 PM");
    assert.equal(
      statusLine([{ key: "cutoff", due_at: "21:00", state: "done", note: null }]),
      "Every step of the day is done.",
    );
  });

  test("waste change is a tenth of a kilogram against the weekday", () => {
    assert.deepEqual(wasteChange({ kg: 0.7, previous_kg: 1.0, previous_date: "2026-10-02" }), {
      change_kg: -0.3,
      versus: "Fri",
    });
    assert.equal(wasteChange({ kg: 0.7, previous_kg: 0.7, previous_date: null }).versus, null);
  });

  test("links point at this console's pages", () => {
    assert.equal(consoleHref("/hub/issues?status=open"), "/refunds?status=open");
    assert.equal(consoleHref("/hub/deliveries"), "/deliveries");
    assert.equal(consoleHref("/community/donations"), "/donations");
  });

  test("one row per kind, with the oldest and due times", () => {
    const [row] = attentionRows([
      {
        kind: "issue_open",
        count: 2,
        oldest_at: "2026-10-06T07:52:00+05:30",
        due_at: "2026-10-06T10:30:00+05:30",
        link: "/hub/issues?status=open",
      },
    ]);
    assert.equal(row.id, "issue_open");
    assert.equal(row.where, "2 items · oldest 7:52 AM · due 10:30 AM");
    assert.equal(row.next.href, "/refunds?status=open");
  });
});
