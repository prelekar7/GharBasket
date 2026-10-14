import { formatDay, formatTime, plural } from "../../../../lib/format.js";

/**
 * GET /v1/analytics/hubs/{hub_id}/today?date= (M12 section 3.3): live counts per stage, from the modules'
 * facades. Timestamps are ISO with an offset, step times are "HH:MM" and weights are kilograms; this file
 * turns them into the words the board shows.
 * @typedef {"cutoff" | "procurement" | "receiving" | "packing" | "dispatch" | "delivery" | "surplus"} StepKey
 * @typedef {"issue_open" | "payment_failed" | "shortage" | "plan_needs_review" | "delivery_held"
 *   | "donations_to_verify" | "surplus_open" | "society_live"} AttentionKind
 * @typedef {{
 *   hub_id: string, date: string, as_of: string,
 *   boxes: { total: number, locked: number, cutoff_at: string,
 *     by_society: { society_id: string, name: string, boxes: number }[] },
 *   packing: { bags_total: number, bags_packed: number, finished_at: string | null },
 *   shortages: { product_id: string, name: string, bags: number }[],
 *   routes: { planned: number, started: number, completed: number, stops_total: number, stops_done: number, late_stops: number },
 *   deliveries: { at_society: number, delivered: number, held: number, undelivered: number },
 *   waste: { kg: number, previous_kg: number, previous_date: string | null },
 *   steps: { key: StepKey, due_at: string, state: "done" | "current" | "issue" | "todo", note: string | null }[],
 *   attention: { kind: AttentionKind, count: number, oldest_at: string | null, due_at: string | null, link: string }[],
 * }} Today
 */

/** @type {Record<StepKey, string>} */
const STEP_LABELS = {
  cutoff: "Cut-off and lock",
  procurement: "Procurement plan sent",
  receiving: "Goods received and graded",
  packing: "Packing",
  dispatch: "Routes published and vans out",
  delivery: "Handovers through captains",
  surplus: "Surplus cascade",
};

/** "21:00" → "9:00 PM". @param {string} hhmm */
export function clock(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}

/**
 * The timeline's steps, in the order the API gives them.
 * @param {Today["steps"]} steps
 */
export function timelineSteps(steps) {
  return steps.map((s) => ({
    label: STEP_LABELS[s.key],
    time: clock(s.due_at),
    note: s.note ?? undefined,
    state: s.state,
  }));
}

/**
 * One line for under the title, made from the steps (the API sends no sentence).
 * @param {Today["steps"]} steps
 */
export function statusLine(steps) {
  const label = (/** @type {Today["steps"][number]} */ s) => STEP_LABELS[s.key];
  const current = steps.find((s) => s.state === "current");
  const next = steps.find((s) => s.state === "todo");
  const issues = steps.filter((s) => s.state === "issue").map(label);
  const where = current
    ? `Now: ${label(current).toLowerCase()}.`
    : next
      ? `Next: ${label(next).toLowerCase()}.`
      : "Every step of the day is done.";
  return issues.length ? `${where} Look at: ${issues.join(", ").toLowerCase()}.` : where;
}

/**
 * "Fri" for the day the waste change is measured against, and the change itself in kilograms (a tenth).
 * @param {Today["waste"]} waste
 */
export function wasteChange(waste) {
  return {
    change_kg: Math.round((waste.kg - waste.previous_kg) * 10) / 10,
    versus: waste.previous_date ? formatDay(waste.previous_date).split(" ")[0] : null,
  };
}

/** @type {Record<AttentionKind, { what: string, tone: "danger" | "accent" | "primary" | "neutral", state: string, next: string }>} */
const ATTENTION = {
  issue_open: { what: "Order issues open", tone: "danger", state: "Open", next: "Open the queue" },
  payment_failed: {
    what: "Payments failed",
    tone: "danger",
    state: "Failed",
    next: "Check the payments",
  },
  shortage: {
    what: "Short at receiving",
    tone: "primary",
    state: "Applied",
    next: "See receiving",
  },
  plan_needs_review: {
    what: "A route plan needs review",
    tone: "accent",
    state: "Review",
    next: "Open the routes",
  },
  delivery_held: {
    what: "Deliveries held",
    tone: "accent",
    state: "Held",
    next: "Open the deliveries",
  },
  donations_to_verify: {
    what: "Donations to verify",
    tone: "accent",
    state: "To verify",
    next: "Verify them",
  },
  surplus_open: {
    what: "Surplus not placed yet",
    tone: "accent",
    state: "Open",
    next: "Open waste and cascade",
  },
  society_live: {
    what: "A society went live",
    tone: "primary",
    state: "Live",
    next: "See the societies",
  },
};

/**
 * The API names console routes under /hub/… and /community/…; this console's pages sit at the top level, and the order
 * issues queue (M4 section 3.6) is the Refunds page until it has its own.
 * @param {string} link e.g. "/hub/issues?status=open"
 */
export function consoleHref(link) {
  const path = link.replace(/^\/(hub|community)\//, "/");
  return path.startsWith("/issues") ? path.replace("/issues", "/refunds") : path;
}

/**
 * The "Needs you" rows: one per kind with a count, the copy coming from the kind.
 * @param {Today["attention"]} attention
 */
export function attentionRows(attention) {
  return attention.map((a) => {
    const copy = ATTENTION[a.kind];
    const parts = [plural(a.count, "item")];
    if (a.oldest_at) parts.push(`oldest ${formatTime(a.oldest_at)}`);
    if (a.due_at) parts.push(`due ${formatTime(a.due_at)}`);
    return {
      id: a.kind,
      what: { text: copy.what, strong: true },
      where: parts.join(" · "),
      state: { badge: { tone: copy.tone, label: copy.state } },
      next: { text: copy.next, href: consoleHref(a.link) },
    };
  });
}
