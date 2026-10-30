// What the Societies page sends to the API (M9 §3.1 and §3.6 as built), kept apart from the Server Actions
// so the rules can be tested. Plain JS, no React.

/**
 * The fields `POST /v1/admin/societies` takes (SocietyCreateIn) and nothing else: `status`, `slug` and
 * `boundary` are 422, and a new society is always a prospect. Fields that are undefined are left out.
 * @param {Record<string, any>} input
 */
export function createBody(input) {
  const { name, area, hub_id, unlock_threshold, flats, towers } = input;
  return clean({ name, area, hub_id, unlock_threshold, flats, towers });
}

/**
 * The fields `PATCH /v1/admin/societies/{id}` takes (SocietyUnlockIn) and nothing else: `name`, `area`,
 * `hub_id` and `flats` are 422, and `status` is unlocking, paused or live (a society cannot be set back to
 * "prospect"). `boundary: null` clears the boundary and is kept; undefined fields are left out.
 * @param {Record<string, any>} patch
 */
export function patchBody(patch) {
  const { unlock_threshold, unlock_threshold_pinned, status, boundary, towers } = patch;
  return clean({
    unlock_threshold,
    unlock_threshold_pinned,
    status: status === "prospect" ? undefined : status,
    boundary,
    towers,
  });
}

/** @param {Record<string, unknown>} fields */
const clean = (fields) =>
  Object.fromEntries(Object.entries(fields).filter(([, v]) => v !== undefined));

/**
 * The statuses a hub can set on a society it already has. A prospect society is listed as such too (it is
 * what it is) but cannot be chosen again once left.
 * @param {string} current
 */
export const statusChoices = (current) => [
  ...(current === "prospect" ? ["prospect"] : []),
  "unlocking",
  "paused",
  "live",
];

/**
 * Only what the edit form changed. Sending an unchanged threshold would flip its source to "hub", and an
 * unchanged tower list is an audit entry for nothing.
 * @param {{ status: string, unlock_threshold: number, towers: { id: string, label: string }[] }} society
 * @param {{ status: string, unlock_threshold: number, towers: { id?: string, label: string }[] }} form
 * @returns {Record<string, any>} `{}` when nothing changed
 */
export function changesOf(society, form) {
  /** @type {Record<string, any>} */
  const changes = {};
  if (form.unlock_threshold !== society.unlock_threshold)
    changes.unlock_threshold = form.unlock_threshold;
  if (form.status !== society.status && form.status !== "prospect") changes.status = form.status;
  const same =
    form.towers.length === society.towers.length &&
    form.towers.every(
      (t, i) => t.id === society.towers[i].id && t.label === society.towers[i].label,
    );
  if (!same)
    changes.towers = form.towers.map((t) =>
      t.id ? { id: t.id, label: t.label } : { label: t.label },
    );
  return changes;
}
