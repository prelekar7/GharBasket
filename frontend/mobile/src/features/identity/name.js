/**
 * A person's name as other people see it: the same rule as the API's `platform/names.py` (M1
 * profile name, onboarding `owner_name`, M8 display name; #492). Trimmed, each run of whitespace
 * becomes one space, then 2 to 40 characters. Characters are Unicode code points, as in Python's
 * `len`, so `[...s].length`, not `s.length` (they differ for emoji and other non-BMP characters).
 */

export const NAME_MIN = 2;
export const NAME_MAX = 40;

/** Trimmed, with inner whitespace collapsed; "" when there was only whitespace. @param {string} value */
export const tidyName = (value) => value.split(/\s+/u).filter(Boolean).join(" ");

/** Whether `value`, once tidied, is 2 to 40 characters. @param {string} value */
export function isValidName(value) {
  const n = [...tidyName(value)].length;
  return n >= NAME_MIN && n <= NAME_MAX;
}
