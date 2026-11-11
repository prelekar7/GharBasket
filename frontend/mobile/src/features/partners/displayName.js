import { ApiError, fieldError } from "@/lib/api.js";

// The name customers see on a partner application: 2 to 40 characters (M8 §3.1,
// ApplicationIn.display_name). Left out, the API falls back to the M1 profile name, which a new
// user may not have set, so the apply form always asks for it.

export const NAME_MIN = 2;
export const NAME_MAX = 40;

/** Spaces collapsed and trimmed, the way the server reads it. @param {string | null | undefined} value */
export const cleanName = (value) => (value ?? "").split(/\s+/).filter(Boolean).join(" ");

/** True when the name is 2 to 40 characters once cleaned. @param {string | null | undefined} value */
export const isValidName = (value) => {
  const n = [...cleanName(value)].length;
  return n >= NAME_MIN && n <= NAME_MAX;
};

/** True when the API refused the name (partners.display_name_invalid, or a 422 on that field). @param {unknown} e */
export const isNameRefused = (e) =>
  e instanceof ApiError &&
  e.status === 422 &&
  (e.code === "partners.display_name_invalid" || fieldError(e, "display_name") !== undefined);
