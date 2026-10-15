// Small helpers shared by the admin mocks (community, identity, tiffin, hubadmin).
import { problem, roleOf } from "./util.js";

/**
 * A 403 problem unless the request's role is one of `roles`, else null. Mirrors how the API gates
 * admin endpoints by role (the nav only hides pages; this is the real check).
 * @param {Request} request
 * @param {string[]} roles
 */
export function deny(request, roles) {
  const role = roleOf(request);
  return role && roles.includes(role)
    ? null
    : problem(403, "auth.forbidden", "You don't have permission to do this");
}

/** "Sai Darshan Apartments" → "sai-darshan-apartments". @param {string} text */
export const slugify = (text) =>
  text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");

/**
 * An ISO timestamp in India (+05:30) on today's date plus `days`, at `hour:minute`. Seed times are
 * relative to today so "Today, 10:42" stays true whenever the mock server starts.
 * @param {number} days
 * @param {number} hour
 * @param {number} minute
 */
export function istDay(days, hour, minute) {
  const noon = new Date(Date.now() + days * 86_400_000);
  const ymd = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(noon);
  const hh = String(hour).padStart(2, "0");
  const mm = String(minute).padStart(2, "0");
  return `${ymd}T${hh}:${mm}:00+05:30`;
}

/**
 * A GeoJSON Polygon (a closed ring) from an open list of [lng, lat] corners.
 * @param {[number, number][]} corners
 */
export const polygon = (corners) => ({
  type: /** @type {const} */ ("Polygon"),
  coordinates: [[...corners, corners[0]]],
});

/**
 * An approximate square around a point, about 330 m across: the seed boundaries.
 * @param {number} lng
 * @param {number} lat
 * @param {number} [d] half the side, in degrees
 */
export const square = (lng, lat, d = 0.0015) => {
  /** @param {number} n */
  const r = (n) => Math.round(n * 1e6) / 1e6;
  return polygon([
    [r(lng - d), r(lat - d)],
    [r(lng + d), r(lat - d)],
    [r(lng + d), r(lat + d)],
    [r(lng - d), r(lat + d)],
  ]);
};

/**
 * Whether a value is a usable GeoJSON Polygon: one ring of at least three distinct corners with
 * finite numbers, closed.
 * @param {any} g
 */
export function validPolygon(g) {
  const ring = g?.type === "Polygon" ? g.coordinates?.[0] : null;
  if (!Array.isArray(ring) || ring.length < 4) return false;
  const [a, b] = [ring[0], ring[ring.length - 1]];
  if (a[0] !== b[0] || a[1] !== b[1]) return false;
  return ring.every((p) => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite));
}
