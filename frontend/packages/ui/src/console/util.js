/** Replaces `{name}` placeholders with values (strings arrive as props, so there is no i18n library here). */
export const fill = (/** @type {string} */ s, /** @type {Record<string, unknown>} */ v) =>
  s.replace(/\{(\w+)\}/g, (m, k) => (v[k] != null ? String(v[k]) : m));

/** Shared look of a focusable control in the console: the token focus ring, no browser outline. */
export const focusRing = "outline-none focus-visible:shadow-focus";
