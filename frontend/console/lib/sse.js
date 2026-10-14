// Reading a server-sent event stream (text/event-stream). No imports, so server code and tests can both use it.

/**
 * The data of the first event named `event` in a stream, parsed as JSON, or null when the stream ends (or `timeoutMs`
 * passes) without one. The stream is cancelled before this returns, so a live endpoint can be polled without holding it open.
 * Events end with a blank line; a line is `field: value`; lines starting with ":" are comments (keep-alives).
 * @param {ReadableStream<Uint8Array>} stream
 * @param {string} event
 * @param {number} [timeoutMs]
 * @returns {Promise<any | null>}
 */
export async function firstEvent(stream, event, timeoutMs = 8000) {
  const reader = stream.getReader();
  const timer = setTimeout(() => reader.cancel().catch(() => {}), timeoutMs);
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return null;
      buffer += decoder.decode(value, { stream: true });
      for (let end = buffer.search(/\r?\n\r?\n/); end >= 0; end = buffer.search(/\r?\n\r?\n/)) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end).replace(/^\r?\n\r?\n/, "");
        const lines = block.split(/\r?\n/).filter((l) => !l.startsWith(":"));
        const name =
          lines
            .find((l) => l.startsWith("event:"))
            ?.slice(6)
            .trim() ?? "message";
        if (name !== event) continue;
        const data = lines
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trim())
          .join("\n");
        return JSON.parse(data);
      }
    }
  } finally {
    clearTimeout(timer);
    reader.cancel().catch(() => {});
  }
}
