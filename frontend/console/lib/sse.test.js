import assert from "node:assert/strict";
import { test } from "node:test";
import { firstEvent } from "./sse.js";

/** A stream that delivers the text in the given pieces. @param {string[]} pieces */
const streamOf = (pieces) =>
  new ReadableStream({
    start(controller) {
      for (const p of pieces) controller.enqueue(new TextEncoder().encode(p));
      controller.close();
    },
  });

test("the first event of the wanted name is returned, whatever came before it", async () => {
  const stream = streamOf([
    ": keep-alive\n\n",
    'event: position\ndata: {"lat":1}\n\n',
    'event: snapshot\ndata: {"route_id":"r1","stops":[]}\n\n',
  ]);
  assert.deepEqual(await firstEvent(stream, "snapshot"), { route_id: "r1", stops: [] });
});

test("an event split across chunks is put back together", async () => {
  const stream = streamOf(["event: snap", 'shot\r\ndata: {"a":', "1}\r\n\r\n"]);
  assert.deepEqual(await firstEvent(stream, "snapshot"), { a: 1 });
});

test("a stream that ends without the event gives null", async () => {
  assert.equal(
    await firstEvent(streamOf(['event: end\ndata: {"reason":"reconnect"}\n\n']), "snapshot"),
    null,
  );
});

test("a stream that stays silent gives null after the timeout", async () => {
  const silent = new ReadableStream({ start() {} });
  assert.equal(await firstEvent(silent, "snapshot", 20), null);
});
