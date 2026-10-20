// The backend the tests run on, from Node: the launcher's /__e2e routes (e2e/backend/live_server.py) and the job
// endpoints, all behind the operator token. The server clock only moves forward; only `reset` goes back.

export const API = process.env.E2E_API ?? "http://127.0.0.1:8020";
export const OPERATOR_TOKEN = "e2e-jobs-token"; // harness.JOBS_TOKEN
export const ORIGINS = { mobile: "http://localhost:5174", console: "http://localhost:3001" };

async function call(method, path, body) {
  const answer = await fetch(`${API}${path}`, {
    method,
    headers: { authorization: `Bearer ${OPERATOR_TOKEN}`, "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await answer.text();
  const json = text ? JSON.parse(text) : null;
  if (!answer.ok) throw new Error(`${method} ${path}: ${answer.status} ${text}`);
  return json;
}

/** An instant as ISO 8601 with an offset, from a Date or a string that already has one. */
const iso = (when) => (when instanceof Date ? when.toISOString() : when);

/**
 * The IST wall-clock time on `day` ("2026-10-27") as a Date, e.g. istAt("2026-10-27", 21) is the cut-off.
 * @param {string} day @param {number} hour @param {number} [minute]
 */
export function istAt(day, hour, minute = 0) {
  const pad = (n) => String(n).padStart(2, "0");
  return new Date(`${day}T${pad(hour)}:${pad(minute)}:00+05:30`);
}

export const stack = {
  /** Back to the seed: a new database from the template, Valkey flushed, the clock at the real now. */
  async reset() {
    return call("POST", "/__e2e/reset");
  },
  /** The server's clock: { now, ist, offset_seconds }. @returns {Promise<{ now: string, ist: string, offset_seconds: number }>} */
  async clock() {
    return call("GET", "/__e2e/clock");
  },
  /** The server's now as a Date. */
  async now() {
    return new Date((await stack.clock()).now);
  },
  /** Move the server clock forward (409 if backwards). @param {Date | string} to */
  async travel(to) {
    return call("POST", "/__e2e/clock", { to: iso(to) });
  },
  /** Trigger a job as the external cron does. @param {string} name @param {Date | string} scheduledFor */
  async job(name, scheduledFor) {
    return call("POST", `/v1/internal/jobs/${name}`, { scheduled_for: iso(scheduledFor) });
  },
  /** Let the after-commit relays finish and run outbox_sweep until nothing is due. */
  async sweep() {
    return call("POST", "/__e2e/sweep");
  },
};
