import { describe, expect, it } from "vitest";
import en from "./en-IN.json";
import hi from "./hi-IN.json";
import mr from "./mr-IN.json";

/** Every leaf as "a.b.0.c" → string. @param {unknown} node @param {string} [path] @returns {Record<string, unknown>} */
function flatten(node, path = "") {
  if (node === null || typeof node !== "object") return { [path]: node };
  return Object.assign(
    {},
    ...Object.entries(node).map(([k, v]) => flatten(v, path ? `${path}.${k}` : k)),
  );
}

/** @type {Record<string, Record<string, unknown>>} */
const flat = { "en-IN": flatten(en), "hi-IN": flatten(hi), "mr-IN": flatten(mr) };
describe("locales", () => {
  it.each(["hi-IN", "mr-IN"])("%s has exactly the keys of en-IN", (lng) => {
    const keys = Object.keys(flat["en-IN"]).sort();
    expect(Object.keys(flat[lng]).sort()).toEqual(keys);
  });

  it.each(Object.keys(flat))("%s has no empty text", (lng) => {
    const empty = Object.entries(flat[lng])
      .filter(([, v]) => typeof v !== "string" || v.trim() === "")
      .map(([k]) => k);
    expect(empty).toEqual([]);
  });
});
